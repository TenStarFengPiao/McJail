from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, Mock, patch

import pytest

from maidanlao.adapters.napcat import (
    NapCatClient,
    NapCatError,
    NapCatSettings,
    extract_message_text,
    normalize_message_event,
)


def group_event(*, mentioned: bool = True) -> dict:
    message = []
    if mentioned:
        message.append({"type": "at", "data": {"qq": "999"}})
    message.append({"type": "text", "data": {"text": "  补考时间？ "}})
    return {
        "time": 1789380000,
        "self_id": 999,
        "post_type": "message",
        "message_type": "group",
        "message_id": 12345,
        "group_id": 456,
        "user_id": 789,
        "message": message,
        "raw_message": "[CQ:at,qq=999] 补考时间？",
        "sender": {"user_id": 789, "nickname": "student"},
    }


def private_event():
    event = group_event()
    event["message_type"] = "private"
    event.pop("group_id")
    return event


def test_normalize_group_message_uses_numeric_qq_ids_as_strings() -> None:
    normalized = normalize_message_event(group_event())

    assert normalized == {
        "platform": "qq",
        "protocol": "onebot11",
        "message_id": "12345",
        "message_source": "group",
        "user_open_id": "789",
        "group_id": "456",
        "content": "补考时间？",
        "mentioned_bot": True,
        "created_at": 1789380000,
    }


def test_extract_message_text_keeps_useful_non_text_markers() -> None:
    event = group_event()
    event["message"].extend(
        [
            {"type": "image", "data": {"file": "x.jpg"}},
            {"type": "at", "data": {"qq": "321", "name": "班长"}},
        ]
    )

    assert extract_message_text(event) == "补考时间？ [图片] @班长"


@pytest.mark.parametrize("post_type", ["notice", "request", "meta_event", "message_sent"])
def test_normalizer_ignores_non_inbound_messages(post_type: str) -> None:
    event = group_event()
    event["post_type"] = post_type
    assert normalize_message_event(event) is None


def test_normalizer_ignores_messages_sent_by_the_bot() -> None:
    event = group_event()
    event["user_id"] = 999
    assert normalize_message_event(event) is None


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


def test_http_client_sends_bearer_token_and_onebot_payload() -> None:
    settings = NapCatSettings("http://127.0.0.1:3000", "ws://127.0.0.1:3001", "secret")
    with patch(
        "maidanlao.adapters.napcat.client.urlopen",
        return_value=FakeResponse({"status": "ok", "retcode": 0, "data": {"message_id": 7}}),
    ) as opened:
        message_id = NapCatClient(settings).send_group("456", "hello")

    request = opened.call_args.args[0]
    assert request.full_url == "http://127.0.0.1:3000/send_group_msg"
    assert request.headers["Authorization"] == "Bearer secret"
    assert json.loads(request.data) == {"group_id": "456", "message": "hello"}
    assert message_id == 7


def test_http_client_rejects_failed_onebot_action() -> None:
    settings = NapCatSettings("http://127.0.0.1:3000", "ws://127.0.0.1:3001")
    response = FakeResponse({"status": "failed", "retcode": 1401, "wording": "权限不足", "data": None})
    with (
        patch("maidanlao.adapters.napcat.client.urlopen", return_value=response),
        pytest.raises(NapCatError, match="1401"),
    ):
        NapCatClient(settings).send_private("789", "hello")


def test_event_deduplication_happens_before_processing() -> None:
    from maidanlao.adapters.napcat import NapCatBot

    bot = NapCatBot(NapCatSettings("http://127.0.0.1:3000", "ws://127.0.0.1:3001"), ":memory:")
    calls = []

    async def capture(message):
        calls.append(message)

    bot._process_message = capture

    async def scenario():
        await bot.handle_event(private_event())
        await bot.handle_event(private_event())

    asyncio.run(scenario())
    assert len(calls) == 1


def test_group_message_without_mention_does_not_answer() -> None:
    from maidanlao.adapters.napcat import NapCatBot

    bot = NapCatBot(NapCatSettings("http://127.0.0.1:3000", "ws://127.0.0.1:3001"), ":memory:")
    bot._send_response = AsyncMock()
    normalized = normalize_message_event(group_event(mentioned=False))

    asyncio.run(bot._process_message(normalized))

    bot._send_response.assert_not_awaited()


def test_websocket_dispatch_filters_invalid_json_and_applies_token():
    from maidanlao.adapters.napcat import NapCatBot

    bot = NapCatBot(NapCatSettings("http://localhost", "ws://localhost/events", "token"), ":memory:")
    bot.client = Mock()
    bot.client.get_login_info.return_value = {"user_id": "999", "nickname": "bot"}
    bot._process_message = AsyncMock()

    class Socket:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def __aiter__(self):
            yield "invalid json"
            yield json.dumps(private_event())
            await asyncio.gather(*bot._tasks)
            raise asyncio.CancelledError()

    with patch("websockets.connect", return_value=Socket()) as connect:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(bot.run_forever())
    bot._process_message.assert_awaited_once()
    assert "access_token=token" in connect.call_args.args[0]
    assert connect.call_args.kwargs["additional_headers"] == {"Authorization": "Bearer token"}


def test_websocket_reconnect_and_cancellation():
    from maidanlao.adapters.napcat import NapCatBot

    bot = NapCatBot(NapCatSettings("http://localhost", "ws://localhost"), ":memory:")

    class Disconnected:
        def __init__(self, error):
            self.error = error

        async def __aenter__(self):
            raise self.error

        async def __aexit__(self, *_):
            return False

    with patch(
        "websockets.connect", side_effect=[Disconnected(OSError()), Disconnected(asyncio.CancelledError())]
    ) as connect:
        with patch("maidanlao.adapters.napcat.bot.asyncio.sleep", new_callable=AsyncMock) as wait:
            with pytest.raises(asyncio.CancelledError):
                asyncio.run(bot.run_forever())
    assert connect.call_count == 2
    wait.assert_awaited_once()


def test_dedupe_capacity_does_not_allow_oldest_duplicate_and_expires():
    from maidanlao.adapters.napcat.bot import _RecentEvents

    recent = _RecentEvents(max_items=2, ttl_seconds=10)
    with patch("maidanlao.adapters.napcat.bot.time.monotonic", return_value=0):
        assert recent.add_if_new("first")
        assert recent.add_if_new("second")
        assert not recent.add_if_new("first")
        assert recent.add_if_new("third")
        assert len(recent._items) == 2
    with patch("maidanlao.adapters.napcat.bot.time.monotonic", return_value=11):
        assert recent.add_if_new("third")
        assert len(recent._items) == 1


def test_qq_process_only_text_and_routes_answer_to_correct_target():
    from maidanlao.adapters.napcat import NapCatBot
    from maidanlao.service import AssistantResponse

    bot = NapCatBot(NapCatSettings("http://localhost", "ws://localhost"), ":memory:")
    bot._send_response = AsyncMock()
    with patch(
        "maidanlao.adapters.napcat.bot.build_response_for_text", return_value=AssistantResponse("答复")
    ) as build:
        asyncio.run(bot.handle_event(private_event()))
    assert build.call_args.args[:5] == ("补考时间？", "private", "789", None, ":memory:")
    assert "12345" in build.call_args.args[5]
    assert bot._send_response.call_args.kwargs["group_id"] is None
    assert bot._send_response.call_args.kwargs["reply_to"] == "12345"
    empty = normalize_message_event(group_event())
    empty["content"] = ""
    asyncio.run(bot._process_message(empty))
    assert bot._send_response.await_count == 1


@pytest.mark.parametrize(
    "event",
    [
        None,
        [],
        {"post_type": "message", "message_type": "group", "user_id": 1},
        {"post_type": "message", "message_type": "private", "sender": []},
        {"post_type": "message", "message_type": "notice"},
    ],
)
def test_malformed_events_are_ignored(event):
    assert normalize_message_event(event) is None
