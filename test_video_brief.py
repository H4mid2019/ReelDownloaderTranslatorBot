import json
from types import SimpleNamespace
import video_brief


def test_build_video_brief_prompt_requests_source_language():
    prompt = video_brief.build_video_brief_prompt("instagram", "caption context")

    assert "same source language" in prompt.lower()
    assert "original spoken language" in prompt.lower()
    assert "return ONLY a JSON object" in prompt


def test_build_video_brief_messages_chunk_long_reports():
    brief = {
        "source_language_name": "Persian",
        "source_language_code": "fa",
        "transcript": "سلام دنیا " * 50,
        "summary": "خلاصه " * 20,
        "key_highlights": ["نکته ۱ " * 10, "نکته ۲ " * 10],
        "takeaways": ["برداشت ۱ " * 10, "برداشت ۲ " * 10],
        "model": "gemini-test",
    }

    messages = video_brief.build_video_brief_messages(
        brief,
        "https://www.instagram.com/reel/abc123/",
        "instagram",
        max_chars=220,
    )

    assert len(messages) > 1
    assert all(len(message) <= 220 for message in messages)
    assert any("Detailed Brief" in message for message in messages)
    assert any("سلام دنیا" in message for message in messages)
    assert any("برداشت ۱" in message for message in messages)


def test_generate_video_brief_uses_configured_model_and_normalizes_output(
    tmp_path, monkeypatch
):
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"fake video bytes")

    monkeypatch.setattr(video_brief, "_GOOGLE_AI_AVAILABLE", True)
    monkeypatch.setattr(video_brief, "GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(video_brief, "GOOGLE_AI_MODEL", "gemini-unit-test")
    monkeypatch.setattr(
        video_brief,
        "types",
        SimpleNamespace(
            UploadFileConfig=lambda **kwargs: SimpleNamespace(**kwargs),
            GenerateContentConfig=lambda **kwargs: SimpleNamespace(**kwargs),
        ),
    )

    uploaded_file = SimpleNamespace(name="files/123", state="READY")
    deleted_names = []
    calls = {}

    class FakeFiles:
        def upload(self, file, config):
            calls["upload_file"] = file
            calls["upload_config"] = config
            return uploaded_file

        def get(self, name):
            return uploaded_file

        def delete(self, name):
            deleted_names.append(name)

    class FakeModels:
        def generate_content(self, model, contents, config):
            calls["model"] = model
            calls["contents"] = contents
            calls["config"] = config
            payload = {
                "source_language_code": "fa",
                "source_language_name": "Persian",
                "transcript": "سلام دنیا",
                "summary": "این یک خلاصه است",
                "key_highlights": ["نکته ۱", "نکته ۲"],
                "takeaways": ["برداشت ۱"],
            }
            # candidates=[] means finish_reason check is safely skipped
            return SimpleNamespace(
                text=json.dumps(payload, ensure_ascii=False),
                candidates=[],
            )

    fake_client = SimpleNamespace(files=FakeFiles(), models=FakeModels())

    result = video_brief.generate_video_brief(
        str(video_path),
        caption_context="context",
        platform="twitter",
        client=fake_client,
    )

    assert result["error"] is None
    assert result["model"] == "gemini-unit-test"
    assert result["source_language_code"] == "fa"
    assert result["source_language_name"] == "Persian"
    assert result["transcript"] == "سلام دنیا"
    assert result["summary"] == "این یک خلاصه است"
    assert result["key_highlights"] == ["نکته ۱", "نکته ۲"]
    assert result["takeaways"] == ["برداشت ۱"]
    assert calls["model"] == "gemini-unit-test"
    assert calls["contents"][0] is uploaded_file
    assert isinstance(calls["contents"][1], str)
    assert deleted_names == ["files/123"]
    # Verify max_output_tokens is always set to prevent truncation
    assert getattr(calls["config"], "max_output_tokens", None) == 65535


def test_generate_video_brief_max_tokens_finish_reason_returns_error(
    tmp_path, monkeypatch
):
    """When Gemini hits MAX_TOKENS, return a clear error before JSON parsing."""
    from google.genai import types as real_types

    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"fake video bytes")

    monkeypatch.setattr(video_brief, "_GOOGLE_AI_AVAILABLE", True)
    monkeypatch.setattr(video_brief, "GEMINI_API_KEY", "fake-key")

    uploaded_file = SimpleNamespace(name="files/abc", state="READY")

    class FakeFiles:
        def upload(self, file, config):
            return uploaded_file

        def get(self, name):
            return uploaded_file

        def delete(self, name):
            pass

    class FakeModels:
        def generate_content(self, model, contents, config):
            # Simulate truncated response: finish_reason is MAX_TOKENS
            candidate = SimpleNamespace(
                finish_reason=real_types.FinishReason.MAX_TOKENS
            )
            return SimpleNamespace(
                text='{"source_language_code": "fa", "transcript": "متن ناقص',  # broken JSON
                candidates=[candidate],
            )

    fake_client = SimpleNamespace(files=FakeFiles(), models=FakeModels())
    result = video_brief.generate_video_brief(
        str(video_path), client=fake_client
    )

    assert result.get("error") is not None
    assert "too long" in result["error"].lower() or "transcript" in result["error"].lower()
    # Must NOT contain a parsed payload
    assert "source_language_code" not in result


def test_generate_video_brief_stop_reason_parses_json_normally(
    tmp_path, monkeypatch
):
    """finish_reason=STOP (normal) must still parse the JSON response."""
    from google.genai import types as real_types

    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"fake video bytes")

    monkeypatch.setattr(video_brief, "_GOOGLE_AI_AVAILABLE", True)
    monkeypatch.setattr(video_brief, "GEMINI_API_KEY", "fake-key")

    uploaded_file = SimpleNamespace(name="files/xyz", state="READY")
    payload = {
        "source_language_code": "en",
        "source_language_name": "English",
        "transcript": "Hello world",
        "summary": "A test",
        "key_highlights": ["point 1"],
        "takeaways": ["do this"],
    }

    class FakeFiles:
        def upload(self, file, config):
            return uploaded_file

        def get(self, name):
            return uploaded_file

        def delete(self, name):
            pass

    class FakeModels:
        def generate_content(self, model, contents, config):
            candidate = SimpleNamespace(finish_reason=real_types.FinishReason.STOP)
            return SimpleNamespace(
                text=json.dumps(payload),
                candidates=[candidate],
            )

    fake_client = SimpleNamespace(files=FakeFiles(), models=FakeModels())
    result = video_brief.generate_video_brief(str(video_path), client=fake_client)

    assert result["error"] is None
    assert result["transcript"] == "Hello world"


def test_generate_video_brief_no_candidates_still_works(tmp_path, monkeypatch):
    """Empty candidates list must not crash — falls through to JSON parsing."""
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"fake video bytes")

    monkeypatch.setattr(video_brief, "_GOOGLE_AI_AVAILABLE", True)
    monkeypatch.setattr(video_brief, "GEMINI_API_KEY", "fake-key")

    uploaded_file = SimpleNamespace(name="files/x", state="READY")
    payload = {
        "source_language_code": "fa",
        "source_language_name": "Persian",
        "transcript": "سلام",
        "summary": "خلاصه",
        "key_highlights": ["نکته"],
        "takeaways": ["برداشت"],
    }

    class FakeFiles:
        def upload(self, file, config):
            return uploaded_file

        def get(self, name):
            return uploaded_file

        def delete(self, name):
            pass

    class FakeModels:
        def generate_content(self, model, contents, config):
            return SimpleNamespace(text=json.dumps(payload), candidates=[])

    fake_client = SimpleNamespace(files=FakeFiles(), models=FakeModels())
    result = video_brief.generate_video_brief(str(video_path), client=fake_client)

    assert result["error"] is None
    assert result["transcript"] == "سلام"
