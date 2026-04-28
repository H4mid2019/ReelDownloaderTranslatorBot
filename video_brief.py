"""
Detailed source-language brief generation for Instagram/X video posts.

This module uses Gemini via the Google Gen AI Python SDK to:
1. Upload the downloaded video file.
2. Ask Gemini to transcribe the speech verbatim.
3. Ask Gemini to write the summary, key highlights, and takeaways in the
   original language of the video (including Persian/Farsi).
4. Return a Telegram-safe, chunked representation for the bot.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import time
from typing import Any, Iterable, Optional

from config import GEMINI_API_KEY, GOOGLE_AI_MODEL

try:
    from google import genai
    from google.genai import types

    _GOOGLE_AI_AVAILABLE = True
except (
    ImportError
):  # pragma: no cover - dependency availability is environment specific
    genai = None  # type: ignore[assignment]
    types = None  # type: ignore[assignment]
    _GOOGLE_AI_AVAILABLE = False

logger = logging.getLogger(__name__)


_LANGUAGE_NAMES = {
    "ar": "Arabic",
    "bg": "Bulgarian",
    "de": "German",
    "en": "English",
    "es": "Spanish",
    "fa": "Persian",
    "fr": "French",
    "hi": "Hindi",
    "id": "Indonesian",
    "it": "Italian",
    "ja": "Japanese",
    "ko": "Korean",
    "nl": "Dutch",
    "pl": "Polish",
    "pt": "Portuguese",
    "ru": "Russian",
    "tr": "Turkish",
    "uk": "Ukrainian",
    "vi": "Vietnamese",
    "zh": "Chinese",
}

_BRIEF_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "source_language_code": {"type": "STRING"},
        "source_language_name": {"type": "STRING"},
        "transcript": {"type": "STRING"},
        "summary": {"type": "STRING"},
        "key_highlights": {
            "type": "ARRAY",
            "items": {"type": "STRING"},
        },
        "takeaways": {
            "type": "ARRAY",
            "items": {"type": "STRING"},
        },
    },
    "required": [
        "source_language_code",
        "source_language_name",
        "transcript",
        "summary",
        "key_highlights",
        "takeaways",
    ],
}

# Same schema plus sentiment object for /dbs
_SENTIMENT_PROPS: dict[str, Any] = {
    "visual_sentiment": {
        "type": "OBJECT",
        "properties": {
            "faces_visible": {"type": "BOOLEAN"},
            "notes": {"type": "STRING"},
        },
        "required": ["faces_visible", "notes"],
    },
    "vocal_sentiment": {
        "type": "OBJECT",
        "properties": {
            "tone": {"type": "STRING"},
            "notes": {"type": "STRING"},
        },
        "required": ["tone", "notes"],
    },
    "text_sentiment": {
        "type": "OBJECT",
        "properties": {
            "overall": {"type": "STRING"},
            "emotions": {"type": "ARRAY", "items": {"type": "STRING"}},
            "notes": {"type": "STRING"},
        },
        "required": ["overall", "emotions", "notes"],
    },
}

_BRIEF_SENTIMENT_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        **_BRIEF_RESPONSE_SCHEMA["properties"],
        **_SENTIMENT_PROPS,
    },
    "required": list(_BRIEF_RESPONSE_SCHEMA["required"]) + [
        "visual_sentiment", "vocal_sentiment", "text_sentiment"
    ],
}


def make_video_brief_cache_key(
    platform: str, post_id: str, model: str = GOOGLE_AI_MODEL,
    with_sentiment: bool = False,
) -> str:
    """Create a stable cache key for `/db` (and `/dbs` when with_sentiment)."""
    suffix = ":sent" if with_sentiment else ""
    return f"brief:{platform}:{post_id}:{model}{suffix}"


def build_video_brief_prompt(
    platform: str,
    caption_context: Optional[str] = None,
) -> str:
    """Build a Gemini prompt that preserves the source language."""
    caption_block = ""
    if caption_context and caption_context.strip():
        caption_block = (
            "\n\nAdditional post context (caption or tweet text):\n"
            f"""{caption_context.strip()}"""
        )

    return (
        "You are an expert video analyst. "
        f"Analyze this {platform} video and return ONLY a JSON object.\n\n"
        "Requirements:\n"
        "- The transcript must be verbatim in the original spoken language.\n"
        "- The summary, key highlights, and takeaways must be written in the SAME source language as the transcript.\n"
        "- Do not translate the analysis into English unless the source language is English.\n"
        "- If the video mixes languages, choose the dominant language and keep the entire report consistent in that language.\n"
        "- Keep key highlights concise and specific.\n"
        "- Keep takeaways actionable and practical.\n"
        "- Return no markdown fences, no commentary, no prose outside JSON.\n"
        "- Do NOT embed markdown (**, ##, -, *) inside any JSON string value.\n\n"
        "JSON shape:\n"
        "{\n"
        '  "source_language_code": "ISO 639-1 code",\n'
        '  "source_language_name": "Human readable language name",\n'
        '  "transcript": "Verbatim transcript in the original language",\n'
        '  "summary": "Brief summary in the original language",\n'
        '  "key_highlights": ["...", "..."],\n'
        '  "takeaways": ["...", "..."]\n'
        "}" + caption_block
    )


def build_video_brief_with_sentiment_prompt(
    platform: str,
    caption_context: Optional[str] = None,
) -> str:
    """Brief + sentiment (visual / vocal / text). Source-language preserving."""
    caption_block = ""
    if caption_context and caption_context.strip():
        caption_block = (
            "\n\nAdditional post context (caption or tweet text):\n"
            f"""{caption_context.strip()}"""
        )

    return (
        "You are an expert multimodal video analyst. "
        f"Analyze this {platform} video and return ONLY a JSON object.\n\n"
        "Requirements:\n"
        "- Transcript verbatim in the original spoken language.\n"
        "- summary, key_highlights, takeaways: written in the SAME source "
        "language as the transcript (do NOT translate).\n"
        "- visual_sentiment, vocal_sentiment, text_sentiment fields below: "
        "always written in English regardless of source language (so the "
        "user can read them quickly).\n"
        "- For visual_sentiment.faces_visible: true only if at least one human "
        "face is clearly visible at some point.\n"
        "- For visual_sentiment.notes: describe observable cues only "
        "(e.g. 'speaker smiles when discussing X', 'tense posture during Y'). "
        "If no faces are visible, set notes to 'No faces visible' and "
        "describe other visual cues if any.\n"
        "- For vocal_sentiment.tone: short label (e.g. 'calm and measured', "
        "'agitated and rapid', 'monotone'). notes: brief explanation.\n"
        "- For text_sentiment.overall: one of "
        "'positive', 'negative', 'neutral', 'mixed'.\n"
        "- For text_sentiment.emotions: short list of detected tones in the "
        "transcript (e.g. ['hopeful', 'sarcastic', 'resigned']).\n"
        "- IMPORTANT: present sentiment as observed cues, not as definitive "
        "psychological diagnosis. Do not over-interpret.\n"
        "- Do NOT embed markdown (**, ##, -, *) inside any JSON string value.\n"
        "- Return no markdown fences, no commentary outside JSON.\n\n"
        "JSON shape:\n"
        "{\n"
        '  "source_language_code": "ISO 639-1",\n'
        '  "source_language_name": "Human readable",\n'
        '  "transcript": "Verbatim, original language",\n'
        '  "summary": "Source-language summary",\n'
        '  "key_highlights": ["...", "..."],\n'
        '  "takeaways": ["...", "..."],\n'
        '  "visual_sentiment": {"faces_visible": true, "notes": "English"},\n'
        '  "vocal_sentiment": {"tone": "English", "notes": "English"},\n'
        '  "text_sentiment": {"overall": "positive|negative|neutral|mixed", '
        '"emotions": ["english", "..."], "notes": "English"}\n'
        "}" + caption_block
    )


def _build_condensed_brief_prompt(
    platform: str,
    caption_context: Optional[str] = None,
) -> str:
    """Fallback prompt for very long videos — requests condensed transcript."""
    caption_block = ""
    if caption_context and caption_context.strip():
        caption_block = (
            "\n\nAdditional post context (caption or tweet text):\n"
            f"""{caption_context.strip()}"""
        )

    return (
        "You are an expert video analyst. "
        f"Analyze this {platform} video and return ONLY a valid JSON object — "
        "no markdown, no prose, no commentary outside the JSON.\n\n"
        "⚠️ CRITICAL LANGUAGE RULE — NON-NEGOTIABLE:\n"
        "Detect the spoken language of the video. "
        "Every single JSON field — transcript, summary, key_highlights, takeaways — "
        "MUST be written in that same spoken language. "
        "If the video is in Persian/Farsi, write ALL fields in Persian/Farsi. "
        "If the video is in Arabic, write ALL fields in Arabic. "
        "NEVER use English for any field unless the video itself is spoken in English. "
        "Translating into English is strictly forbidden.\n\n"
        "Additional requirements:\n"
        "- transcript: condensed key-points (max ~800 words), NOT verbatim — "
        "summarize repetitive sections but preserve the original spoken language.\n"
        "- Do NOT embed markdown (**, ##, -, *) inside any JSON string value.\n"
        "- Keep key_highlights concise and specific.\n"
        "- Keep takeaways actionable and practical.\n\n"
        "JSON shape:\n"
        "{\n"
        '  "source_language_code": "ISO 639-1 code e.g. fa",\n'
        '  "source_language_name": "Human readable e.g. Persian",\n'
        '  "transcript": "Condensed transcript in the SPOKEN language of the video",\n'
        '  "summary": "Summary in the SPOKEN language of the video",\n'
        '  "key_highlights": ["highlight in spoken language", "..."],\n'
        '  "takeaways": ["takeaway in spoken language", "..."]\n'
        "}" + caption_block
    )


def _friendly_error_message(exc: Exception) -> str:
    error_str = str(exc).lower()
    if any(
        phrase in error_str
        for phrase in ["private", "permission", "access denied", "403", "forbidden"]
    ):
        return "❌ I can only process public videos. Please check the post privacy settings."
    if any(
        phrase in error_str
        for phrase in [
            "context_length_exceeded",
            "too long",
            "token limit",
            "max tokens",
            "resource_exhausted",
        ]
    ):
        return "⚠️ This video is too long for me to process right now. Please submit a shorter video."
    if any(
        phrase in error_str for phrase in ["safety", "age", "restricted", "blocked"]
    ):
        return "⚠️ This video appears to be age-restricted or contains content I cannot process."
    if any(phrase in error_str for phrase in ["404", "not found", "unavailable"]):
        return "❌ This video is not available or has been removed."
    logger.error("Gemini video-brief error: %s", exc, exc_info=True)
    return (
        "❌ Failed to generate a detailed brief for this video. Please try again later."
    )


def _guess_mime_type(video_path: str) -> str:
    mime_type, _ = mimetypes.guess_type(video_path)
    if mime_type:
        return mime_type
    ext = os.path.splitext(video_path)[1].lower()
    if ext in {".mov", ".qt"}:
        return "video/quicktime"
    if ext in {".webm"}:
        return "video/webm"
    return "video/mp4"


def _wait_for_file_processing(
    client: Any, uploaded_file: Any, timeout_seconds: int = 120
) -> Any:
    """Poll the Gemini Files API until the uploaded file is ready."""
    deadline = time.monotonic() + timeout_seconds
    current_file = uploaded_file

    while True:
        state = str(getattr(current_file, "state", "")).upper()
        if "PROCESSING" not in state:
            return current_file
        if time.monotonic() >= deadline:
            raise TimeoutError(
                "Timed out waiting for Gemini to process the uploaded video"
            )
        time.sleep(2)
        current_file = client.files.get(name=current_file.name)


def _normalize_string_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    normalized: list[str] = []
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            normalized.append(text)
    return normalized


def _normalize_sentiment(payload: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Extract sentiment fields if present; return None when absent."""
    vis = payload.get("visual_sentiment")
    voc = payload.get("vocal_sentiment")
    txt = payload.get("text_sentiment")
    if not (isinstance(vis, dict) or isinstance(voc, dict) or isinstance(txt, dict)):
        return None
    return {
        "visual": {
            "faces_visible": bool(vis.get("faces_visible")) if isinstance(vis, dict) else False,
            "notes": str(vis.get("notes") or "").strip() if isinstance(vis, dict) else "",
        },
        "vocal": {
            "tone": str(voc.get("tone") or "").strip() if isinstance(voc, dict) else "",
            "notes": str(voc.get("notes") or "").strip() if isinstance(voc, dict) else "",
        },
        "text": {
            "overall": str(txt.get("overall") or "").strip().lower() if isinstance(txt, dict) else "",
            "emotions": _normalize_string_list(txt.get("emotions")) if isinstance(txt, dict) else [],
            "notes": str(txt.get("notes") or "").strip() if isinstance(txt, dict) else "",
        },
    }


def _normalize_response(payload: dict[str, Any]) -> dict[str, Any]:
    source_language_code = (
        str(payload.get("source_language_code") or payload.get("language_code") or "")
        .strip()
        .lower()
    )
    source_language_name = str(
        payload.get("source_language_name")
        or payload.get("language_name")
        or _LANGUAGE_NAMES.get(
            source_language_code, source_language_code.upper() or "Unknown"
        )
    ).strip()
    transcript = str(payload.get("transcript") or "").strip()
    summary = str(payload.get("summary") or "").strip()
    key_highlights = _normalize_string_list(payload.get("key_highlights"))
    takeaways = _normalize_string_list(payload.get("takeaways"))

    out: dict[str, Any] = {
        "source_language_code": source_language_code,
        "source_language_name": source_language_name,
        "transcript": transcript,
        "summary": summary,
        "key_highlights": key_highlights,
        "takeaways": takeaways,
    }
    sentiment = _normalize_sentiment(payload)
    if sentiment is not None:
        out["sentiment"] = sentiment
    return out


def generate_video_brief(
    video_path: str,
    caption_context: Optional[str] = None,
    platform: str = "instagram",
    model: Optional[str] = None,
    client: Any | None = None,
    with_sentiment: bool = False,
) -> dict[str, Any]:
    """Generate transcript, summary, highlights and takeaways for a video.

    When with_sentiment=True, also returns visual / vocal / text sentiment
    observations (used by the /dbs command).
    """
    if not os.path.exists(video_path):
        return {"error": f"File not found: {video_path}"}

    if not _GOOGLE_AI_AVAILABLE:
        return {
            "error": "google-genai library not installed. Run: pip install google-genai",
        }

    if not GEMINI_API_KEY:
        return {"error": "GEMINI_API_KEY not configured in .env"}

    client = client or genai.Client(api_key=GEMINI_API_KEY)
    model_name = model or GOOGLE_AI_MODEL
    schema = (_BRIEF_SENTIMENT_RESPONSE_SCHEMA if with_sentiment
              else _BRIEF_RESPONSE_SCHEMA)
    uploaded_file = None

    try:
        uploaded_file = client.files.upload(
            file=video_path,
            config=types.UploadFileConfig(mime_type=_guess_mime_type(video_path)),
        )
        uploaded_file = _wait_for_file_processing(client, uploaded_file)

        prompt = (build_video_brief_with_sentiment_prompt(platform, caption_context)
                  if with_sentiment
                  else build_video_brief_prompt(platform, caption_context))
        response = client.models.generate_content(
            model=model_name,
            contents=[uploaded_file, prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_json_schema=schema,
                temperature=0.2,
                max_output_tokens=65535,
            ),
        )

        # Detect truncation before touching response.text — a MAX_TOKENS finish
        # means the JSON was cut off mid-stream and will never parse successfully.
        # Retry once with a condensed-transcript prompt before giving up.
        candidate = (response.candidates or [None])[0]
        if candidate is not None:
            finish_reason = getattr(candidate, "finish_reason", None)
            if finish_reason == types.FinishReason.MAX_TOKENS:
                logger.warning(
                    "Gemini hit MAX_TOKENS for video brief (model=%s). "
                    "Retrying with condensed transcript prompt.",
                    model_name,
                )
                condensed_prompt = _build_condensed_brief_prompt(
                    platform, caption_context
                )
                response = client.models.generate_content(
                    model=model_name,
                    contents=[uploaded_file, condensed_prompt],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_json_schema=_BRIEF_RESPONSE_SCHEMA,
                        temperature=0.2,
                        max_output_tokens=65535,
                    ),
                )
                retry_candidate = (response.candidates or [None])[0]
                if retry_candidate is not None:
                    retry_finish = getattr(retry_candidate, "finish_reason", None)
                    if retry_finish == types.FinishReason.MAX_TOKENS:
                        # Escalate to full flash model which has a much larger
                        # output window (~65K tokens vs ~8K for flash-lite).
                        escalation_model = "gemini-2.5-flash"
                        if model_name == escalation_model:
                            # Already on the big model — nothing left to try.
                            logger.warning(
                                "Condensed retry also hit MAX_TOKENS (model=%s).",
                                model_name,
                            )
                            return {
                                "error": (
                                    "⚠️ The video transcript is too long to process in one pass. "
                                    "Try a shorter clip or a model with a larger output window."
                                )
                            }
                        logger.warning(
                            "Condensed retry hit MAX_TOKENS on %s. Escalating to %s.",
                            model_name,
                            escalation_model,
                        )
                        response = client.models.generate_content(
                            model=escalation_model,
                            contents=[uploaded_file, condensed_prompt],
                            config=types.GenerateContentConfig(
                                response_mime_type="application/json",
                                response_json_schema=_BRIEF_RESPONSE_SCHEMA,
                                temperature=0.2,
                                max_output_tokens=65535,
                            ),
                        )
                        esc_candidate = (response.candidates or [None])[0]
                        if esc_candidate is not None:
                            esc_finish = getattr(esc_candidate, "finish_reason", None)
                            if esc_finish == types.FinishReason.MAX_TOKENS:
                                logger.warning(
                                    "Escalated model %s also hit MAX_TOKENS.",
                                    escalation_model,
                                )
                                return {
                                    "error": (
                                        "⚠️ The video transcript is too long to process in one pass. "
                                        "Try a shorter clip or a model with a larger output window."
                                    )
                                }
                        model_name = escalation_model

        raw_text = (response.text or "").strip()
        if not raw_text:
            return {
                "error": "Gemini returned an empty response while generating the brief."
            }

        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError:
            # Remove accidental markdown fences or leading/trailing commentary.
            stripped = re.sub(
                r"^```(?:json)?|```$",
                "",
                raw_text.strip(),
                flags=re.IGNORECASE | re.MULTILINE,
            ).strip()
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError:
                logger.warning(
                    "Gemini returned malformed JSON (truncated?). raw_text[:300]=%r",
                    raw_text[:300],
                )
                return {
                    "error": (
                        "⚠️ Gemini returned an incomplete response. "
                        "The video may be too long or complex — please try again."
                    )
                }

        normalized = _normalize_response(payload)
        if not normalized["transcript"]:
            return {"error": "Gemini did not return a transcript for this video."}
        if not normalized["summary"]:
            return {"error": "Gemini did not return a summary for this video."}

        normalized.update(
            {
                "platform": platform,
                "model": model_name,
                "error": None,
            }
        )
        return normalized

    except Exception as exc:
        return {"error": _friendly_error_message(exc)}
    finally:
        if uploaded_file is not None:
            try:
                uploaded_name: Optional[str] = getattr(uploaded_file, "name", None)
                if isinstance(uploaded_name, str) and uploaded_name:
                    client.files.delete(name=uploaded_name)
            except Exception:
                pass


def _split_telegram_message(text: str, max_chars: int = 3900) -> list[str]:
    """Split long text into Telegram-safe chunks while preserving paragraphs."""
    if len(text) <= max_chars:
        return [text]

    paragraphs = [
        paragraph.strip() for paragraph in text.split("\n\n") if paragraph.strip()
    ]
    chunks: list[str] = []
    current = ""

    def flush_current() -> None:
        nonlocal current
        if current.strip():
            chunks.append(current.strip())
        current = ""

    for paragraph in paragraphs:
        candidate = paragraph if not current else f"{current}\n\n{paragraph}"
        if len(candidate) <= max_chars:
            current = candidate
            continue

        flush_current()

        if len(paragraph) <= max_chars:
            current = paragraph
            continue

        # Hard-split very long paragraphs by line, then by fixed window if necessary.
        lines = paragraph.splitlines() or [paragraph]
        line_buffer = ""
        for line in lines:
            line_candidate = line if not line_buffer else f"{line_buffer}\n{line}"
            if len(line_candidate) <= max_chars:
                line_buffer = line_candidate
                continue
            if line_buffer.strip():
                chunks.append(line_buffer.strip())
            line_buffer = line
            if len(line_buffer) > max_chars:
                while len(line_buffer) > max_chars:
                    chunks.append(line_buffer[:max_chars].strip())
                    line_buffer = line_buffer[max_chars:]
        current = line_buffer

    flush_current()
    return chunks or [text[:max_chars]]


def _format_bullets(title: str, items: Iterable[str]) -> str:
    item_list = [item for item in items if item.strip()]
    if not item_list:
        return f"{title}\nNo items returned."
    bullet_lines = "\n".join(f"• {item}" for item in item_list)
    return f"{title}\n{bullet_lines}"


def _format_sentiment_section(sentiment: dict[str, Any]) -> str:
    vis = sentiment.get("visual", {}) or {}
    voc = sentiment.get("vocal", {}) or {}
    txt = sentiment.get("text", {}) or {}

    lines = ["🎭 Sentiment & Tone (observed cues — not definitive)"]

    faces = "yes" if vis.get("faces_visible") else "no"
    vis_notes = str(vis.get("notes") or "").strip() or "—"
    lines.append(f"• Visual ({faces} faces visible): {vis_notes}")

    voc_tone = str(voc.get("tone") or "").strip() or "—"
    voc_notes = str(voc.get("notes") or "").strip()
    lines.append(f"• Vocal: {voc_tone}" + (f" — {voc_notes}" if voc_notes else ""))

    overall = str(txt.get("overall") or "").strip() or "—"
    emotions = _normalize_string_list(txt.get("emotions"))
    txt_notes = str(txt.get("notes") or "").strip()
    emo_part = f" [{', '.join(emotions)}]" if emotions else ""
    lines.append(f"• Text: {overall}{emo_part}" + (f" — {txt_notes}" if txt_notes else ""))

    return "\n".join(lines)


def build_video_brief_messages(
    brief: dict[str, Any],
    post_url: str,
    platform: str,
    max_chars: int = 3900,
) -> list[str]:
    """Format a brief into Telegram-safe messages."""
    if brief.get("error"):
        return [str(brief["error"])]

    source_language_name = str(brief.get("source_language_name") or "Unknown")
    source_language_code = str(brief.get("source_language_code") or "").strip().lower()
    transcript = str(brief.get("transcript") or "").strip()
    summary = str(brief.get("summary") or "").strip()
    key_highlights = _normalize_string_list(brief.get("key_highlights"))
    takeaways = _normalize_string_list(brief.get("takeaways"))
    sentiment = brief.get("sentiment") if isinstance(brief.get("sentiment"), dict) else None
    model_name = str(brief.get("model") or GOOGLE_AI_MODEL)

    header = (
        f"🎬 Detailed Brief\n"
        f"• Platform: {platform.title()}\n"
        f"• Language: {source_language_name}"
    )
    if source_language_code:
        header += f" ({source_language_code})"
    header += f"\n• Model: {model_name}\n"
    if post_url:
        header += f"🔗 Open original post: {post_url}\n"

    sections = [
        header.strip(),
        "━━━━━━━━━━━━━━━━━━━━",
        f"📝 Transcript\n{transcript or 'No transcript returned.'}",
        "━━━━━━━━━━━━━━━━━━━━",
        f"📌 Summary\n{summary or 'No summary returned.'}",
        "━━━━━━━━━━━━━━━━━━━━",
        _format_bullets("✨ Key Highlights", key_highlights),
        "━━━━━━━━━━━━━━━━━━━━",
        _format_bullets("🎯 Takeaways", takeaways),
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    if sentiment:
        sections.append(_format_sentiment_section(sentiment))
        sections.append("━━━━━━━━━━━━━━━━━━━━")

    report = "\n\n".join(section for section in sections if section)
    return _split_telegram_message(report, max_chars=max_chars)
