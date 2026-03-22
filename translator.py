"""
Language detection and translation using Groq LLM API.
Handles Bulgarian to English translation and language detection.
"""
import groq
from config import GROQ_API_KEY, TRANSLATION_MODEL


class Translator:
    """Handles language detection and translation using Groq LLM."""
    
    # Language codes and names mapping
    BULGARIAN_CODES = {'bg', 'bul', 'bulgarian'}
    ENGLISH_CODES = {'en', 'eng', 'english'}
    LIKELY_LANGUAGES = {'bg', 'en'}  # Most expected languages
    
    def __init__(self):
        self.client = groq.Groq(api_key=GROQ_API_KEY)
        self.model = TRANSLATION_MODEL
    
    def detect_language(self, text: str) -> dict:
        """
        Detect the language of the given text.
        Prioritizes English and Bulgarian as these are the expected languages.
        
        Args:
            text: The text to analyze
        
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
        
        # Short text might not have enough information
        if len(text.strip()) < 10:
            return {
                'language': 'en',
                'language_name': 'English',
                'confidence': 0.5,
                'error': None,
                'note': 'Text too short for reliable detection, defaulting to English'
            }
        
        prompt = f"""Analyze the following text and determine if it is in:
1. Bulgarian (bg) - Uses Cyrillic alphabet: а б в г д е ж з и й к л м н о п р с т у ф х ц ч ш щ ъ ь ю я
2. English (en) - Uses Latin alphabet
3. Another language

Text to analyze:
\"\"\"{text[:500]}\"\"\"  (showing first 500 characters)

Respond ONLY with one of these exact formats:
- "LANGUAGE: english" (if it's English)
- "LANGUAGE: bulgarian" (if it's Bulgarian)
- "LANGUAGE: bulgarian" (if it contains Cyrillic characters)
- "LANGUAGE: other" (if it's another language)

Be especially careful - if the text contains ANY Cyrillic characters, it's Bulgarian."""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a language detection assistant. Respond with only the language format requested."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
                max_tokens=20
            )
            
            result = response.choices[0].message.content.strip().lower()
            
            # Parse the response
            if 'bulgarian' in result:
                return {
                    'language': 'bg',
                    'language_name': 'Bulgarian',
                    'confidence': 0.95,
                    'error': None
                }
            elif 'english' in result:
                return {
                    'language': 'en',
                    'language_name': 'English',
                    'confidence': 0.95,
                    'error': None
                }
            else:
                return {
                    'language': 'other',
                    'language_name': 'Other',
                    'confidence': 0.5,
                    'error': None
                }
                
        except Exception as e:
            return {
                'language': None,
                'language_name': None,
                'confidence': 0,
                'error': f"Language detection error: {str(e)}"
            }
    
    def translate_bulgarian_to_english(self, text: str) -> dict:
        """
        Translate Bulgarian text to English.
        
        Args:
            text: Bulgarian text to translate
        
        Returns:
            dict with 'translation', 'source_lang', 'target_lang', 'error'
        """
        if not text or not text.strip():
            return {
                'translation': '',
                'source_lang': 'bg',
                'target_lang': 'en',
                'error': 'No text provided for translation'
            }
        
        prompt = f"""Translate the following Bulgarian text to English. 
Maintain the meaning, tone, and style of the original.
If there are any names, keep them as is.
If there are any phrases that don't translate directly, provide a natural English equivalent.

Bulgarian text:
\"\"\"{text}\"\"\"

Provide only the English translation, nothing else."""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a professional translator specializing in Bulgarian to English translation. Provide accurate, natural translations."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=2000
            )
            
            translation = response.choices[0].message.content.strip()
            
            return {
                'translation': translation,
                'source_lang': 'bg',
                'target_lang': 'en',
                'error': None
            }
            
        except groq.RateLimitError:
            return {
                'translation': '',
                'source_lang': 'bg',
                'target_lang': 'en',
                'error': "Rate limit exceeded. Please try again later."
            }
        except Exception as e:
            return {
                'translation': '',
                'source_lang': 'bg',
                'target_lang': 'en',
                'error': f"Translation error: {str(e)}"
            }
    
    def process_transcript(self, transcript: str, hint_language: str = None) -> dict:
        """
        Process a transcript: detect language and translate if Bulgarian.
        
        Args:
            transcript: The transcribed text
            hint_language: Optional language hint from Whisper
        
        Returns:
            dict with all relevant information
        """
        result = {
            'original_transcript': transcript,
            'detected_language': None,
            'detected_language_name': None,
            'is_bulgarian': False,
            'is_english': False,
            'english_translation': None,
            'final_output': None,
            'error': None
        }
        
        if not transcript or not transcript.strip():
            result['error'] = 'Empty transcript'
            return result
        
        # First, try to detect language
        if hint_language:
            # Use hint if provided (from Whisper)
            if hint_language.lower() in self.BULGARIAN_CODES:
                result['detected_language'] = 'bg'
                result['detected_language_name'] = 'Bulgarian'
            elif hint_language.lower() in self.ENGLISH_CODES:
                result['detected_language'] = 'en'
                result['detected_language_name'] = 'English'
            else:
                # Try detection anyway
                detection = self.detect_language(transcript)
                result['detected_language'] = detection['language']
                result['detected_language_name'] = detection['language_name']
                result['error'] = detection['error']
        else:
            # Detect language
            detection = self.detect_language(transcript)
            result['detected_language'] = detection['language']
            result['detected_language_name'] = detection['language_name']
            result['error'] = detection['error']
        
        # Determine if Bulgarian or English
        if result['detected_language'] in self.BULGARIAN_CODES:
            result['is_bulgarian'] = True
            result['is_english'] = False
            
            # Translate to English
            translation_result = self.translate_bulgarian_to_english(transcript)
            result['english_translation'] = translation_result['translation']
            result['final_output'] = translation_result['translation']
            if translation_result['error']:
                result['error'] = translation_result['error']
                
        elif result['detected_language'] in self.ENGLISH_CODES:
            result['is_bulgarian'] = False
            result['is_english'] = True
            result['final_output'] = transcript  # No translation needed
            
        else:
            # Unknown language - still try to translate if possible
            result['is_bulgarian'] = False
            result['is_english'] = False
            translation_result = self.translate_bulgarian_to_english(transcript)
            result['english_translation'] = translation_result['translation']
            result['final_output'] = transcript + "\n\n(Note: Language could not be determined. Above is the original transcript.)"
            if translation_result['error']:
                result['error'] = translation_result['error']
        
        return result


def detect_and_translate(transcript: str, hint_language: str = None) -> dict:
    """
    Quick helper function for detecting language and translating.
    
    Args:
        transcript: The text to process
        hint_language: Optional language hint
    
    Returns:
        dict with processing results
    """
    translator = Translator()
    return translator.process_transcript(transcript, hint_language)
