import asyncio
from types import SimpleNamespace

import bot


class FakeReplyMessage:
    def __init__(self, parent):
        self.parent = parent
        self.edits = []
        self.deleted = False

    async def edit_text(self, text):
        self.edits.append(text)
        self.parent.edits.append(text)

    async def delete(self):
        self.deleted = True
        self.parent.deleted_messages += 1


class FakeMessage:
    def __init__(self, text="", from_user=True):
        self.text = text
        self.caption = None
        self.from_user = (
            SimpleNamespace(id=123, first_name="Tester") if from_user else None
        )
        self.chat = SimpleNamespace(id=456, title="Test Chat", type="private")
        self.reply_text_calls = []
        self.reply_video_calls = []
        self.edits = []
        self.deleted_messages = 0

    async def reply_text(self, text, **kwargs):
        self.reply_text_calls.append(text)
        return FakeReplyMessage(self)

    async def reply_video(self, **kwargs):
        self.reply_video_calls.append(kwargs)
        return None


class FakeUpdate:
    def __init__(self, message):
        self.message = message


class FakeContext:
    def __init__(self, args):
        self.args = args


def test_download_detailed_command_without_args_shows_usage():
    message = FakeMessage()
    update = FakeUpdate(message)
    context = FakeContext([])

    asyncio.run(bot.download_detailed_command(update, context))

    assert message.reply_text_calls
    assert any("Usage: /df <url>" in text for text in message.reply_text_calls)


def test_process_detailed_url_rejects_unsupported_url():
    message = FakeMessage()
    update = FakeUpdate(message)
    context = FakeContext([])

    asyncio.run(bot.process_detailed_url(update, context, "https://example.com/video"))

    assert message.reply_text_calls
    assert any("Unsupported URL for /df" in text for text in message.reply_text_calls)


def test_process_detailed_url_sends_brief_in_source_language(monkeypatch, tmp_path):
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"fake video bytes")

    message = FakeMessage()
    update = FakeUpdate(message)
    context = FakeContext(["https://www.instagram.com/reel/ABC123/"])

    download_calls = []
    send_calls = []

    def fake_download_video(url):
        download_calls.append(url)
        return SimpleNamespace(
            error=None,
            media_type="video",
            file_path=str(video_path),
            file_paths=[str(video_path)],
            file_size_bytes=video_path.stat().st_size,
            caption="کپشن نمونه",
            tweet_text=None,
            post_url=url,
            platform="instagram",
        )

    async def fake_send_video_or_chunks(*args, **kwargs):
        send_calls.append((args, kwargs))
        return True

    def fake_generate_video_brief(*args, **kwargs):
        return {
            "source_language_code": "fa",
            "source_language_name": "Persian",
            "transcript": "سلام دنیا",
            "summary": "این یک خلاصه است",
            "key_highlights": ["نکته ۱", "نکته ۲"],
            "takeaways": ["برداشت ۱"],
            "platform": "instagram",
            "model": "gemini-test",
            "error": None,
        }

    monkeypatch.setattr(bot, "download_video", fake_download_video)
    monkeypatch.setattr(bot, "send_video_or_chunks", fake_send_video_or_chunks)
    monkeypatch.setattr(bot, "generate_video_brief", fake_generate_video_brief)
    monkeypatch.setattr(bot, "_cache", None)

    asyncio.run(bot.process_detailed_url(update, context, context.args[0]))

    assert download_calls == [context.args[0]]
    assert len(send_calls) == 1
    assert any("Detailed Brief" in text for text in message.reply_text_calls)
    assert any("سلام دنیا" in text for text in message.reply_text_calls)
    assert any("این یک خلاصه است" in text for text in message.reply_text_calls)
    assert any("برداشت ۱" in text for text in message.reply_text_calls)
