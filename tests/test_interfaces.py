"""真实 HTTP 后台、CLI 与 QQ 回复边界测试，不连接外部服务。"""

import asyncio
import json
import threading
from contextlib import contextmanager
from io import BytesIO
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest

from maidanlao.api.server import create_admin_server, parse_import
from maidanlao.cli import main, run_all, run_smoke
from maidanlao.rag import LocalHashEmbeddingClient
from maidanlao.service import AssistantResponse


@contextmanager
def backend(tmp_path, embedder=None, token="test"):
    with create_admin_server(
        "127.0.0.1",
        0,
        str(tmp_path / "http.db"),
        token,
        embedder if embedder is not None else LocalHashEmbeddingClient(),
    ) as server:
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            thread.join()


def request(base, path, payload=None, token="test", method=None, headers=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = Request(
        base + path,
        data=data,
        method=method,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json", **(headers or {})},
    )
    try:
        response = urlopen(req, timeout=3)
    except HTTPError as exc:
        response = exc
    with response:
        raw = response.read()
        return response.code, raw.decode() if "text/html" in response.headers.get("Content-Type", "") else json.loads(
            raw
        )


FIELDS = {
    "title": "<script>bad</script>",
    "source": "教学办",
    "publish_date": "2026-10-01",
    "category": "补考通知",
    "raw_text": "补考报名截止周五。",
}


def test_admin_auth_import_list_reindex_and_removed_routes(tmp_path):
    with backend(tmp_path) as base:
        assert request(base, "/health", token="")[0] == 200
        assert request(base, "/admin/documents", token="wrong")[0] == 401
        assert request(base, "/admin/documents?token=test", headers={"Authorization": ""})[0] == 200
        assert request(base, "/admin/documents/import", FIELDS, token="wrong")[0] == 401
        status, imported = request(base, "/admin/documents/import", FIELDS)
        assert status == 201
        status, listing = request(base, "/admin/documents")
        assert status == 200 and len(listing["documents"]) == 1
        doc = listing["documents"][0]
        assert "raw_text" not in doc
        assert request(base, f"/admin/documents/{imported['document_id']}/reindex", {}, method="POST")[0] == 200
        assert request(base, "/admin/documents")[1]["documents"][0]["version"] == 2
        assert request(base, "/admin/documents/missing/reindex", {}, method="POST")[0] == 404
        status, html = request(base, "/admin")
        assert status == 200 and "&lt;script&gt;" in html and "<script>bad</script>" not in html
        for route in ["/admin/users", "/admin/forward-routes", "/assets/download/old", "/chat", "/admin/assets/upload"]:
            assert request(base, route)[0] == 404
            assert request(base, route, {}, method="POST")[0] == 404


def test_admin_empty_token_and_invalid_input(tmp_path):
    with backend(tmp_path, token="") as base:
        assert request(base, "/health")[0] == 503
        assert request(base, "/admin/documents")[0] == 401
    with backend(tmp_path) as base:
        for fields in [{}, {**FIELDS, "publish_date": "invalid"}, {**FIELDS, "category": "unknown"}]:
            assert request(base, "/admin/documents/import", fields)[0] == 400
        # 只发送请求头，验证服务不等待非法长度或超限正文；避免制造未声明的额外字节。
        assert request(base, "/admin/documents/import", method="POST", headers={"Content-Length": "bad"})[0] == 400
        assert (
            request(base, "/admin/documents/import", method="POST", headers={"Content-Length": str(3 * 1024 * 1024)})[0]
            == 413
        )


def test_admin_cloud_failure_does_not_publish_document(tmp_path):
    broken = Mock(model_name="cloud")
    broken.embed.side_effect = RuntimeError("secret-provider-message")
    with backend(tmp_path, broken) as base:
        status, result = request(base, "/admin/documents/import", FIELDS)
        assert status == 503 and result == {"error": "document_index_failed"}
        assert request(base, "/admin/documents")[1] == {"documents": []}


def test_import_utf8_markdown_multipart_and_form():
    raw = (
        '--bound\r\nContent-Disposition: form-data; name="file"; filename="政策.md"\r\n\r\n补考政策\r\n'
        + "".join(
            f'--bound\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'
            for key, value in FIELDS.items()
            if key != "raw_text"
        )
        + "--bound--\r\n"
    ).encode()
    assert parse_import(raw, "multipart/form-data; boundary=bound")["raw_text"] == "补考政策"
    from urllib.parse import urlencode

    assert parse_import(urlencode(FIELDS).encode(), "application/x-www-form-urlencoded")["scope"] == "school"
    with pytest.raises(ValueError, match="only_utf8"):
        parse_import(raw.replace("政策.md".encode(), b"bad.pdf"), "multipart/form-data; boundary=bound")
    with pytest.raises(ValueError):
        parse_import(b"[]", "application/json")
    with pytest.raises(UnicodeError):
        parse_import(b"\xff", "application/json")


def test_cli_smoke_and_document_commands(tmp_path, monkeypatch, capsys):
    path = str(tmp_path / "cli.db")
    monkeypatch.setenv("DATABASE_URL", path)
    monkeypatch.setattr("maidanlao.cli.LangChainEmbeddingClient", LocalHashEmbeddingClient)
    source = tmp_path / "notice.md"
    source.write_text(FIELDS["raw_text"], encoding="utf-8")
    assert (
        main(
            [
                "import-doc",
                "--file",
                str(source),
                "--title",
                "补考通知",
                "--source",
                "教学办",
                "--publish-date",
                "2026-10-01",
                "--category",
                "补考通知",
            ]
        )
        == 0
    )
    doc_id = json.loads(capsys.readouterr().out)["document_id"]
    assert main(["list-docs"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["id"] == doc_id
    assert main(["reingest-doc", "--document-id", doc_id]) == 0
    assert main(["eval-rag"]) == 1  # 无评测用例，不谎报通过
    assert main(["eval-rag-regression"]) == 1
    assert main(["reingest-doc", "--document-id", "missing"]) == 1
    assert main(["smoke"]) == 0
    assert run_smoke()["status"] == "ok"


def test_cli_entrypoints_use_only_two_components(monkeypatch, capsys):
    app = Mock()
    app.handle_text.return_value = "答复"
    monkeypatch.setattr("maidanlao.cli.AssistantService", Mock(return_value=app))
    assert main(["ask", "--open-id", "1", "--source", "private", "--text", "你好"]) == 0
    assert "答复" in capsys.readouterr().out
    with patch("maidanlao.adapters.napcat.NapCatBot") as bot:
        assert main(["run-bot"]) == 0
        bot.return_value.run.assert_called_once()
    with patch("maidanlao.api.server.run_admin_server") as admin:
        assert main(["run-admin", "--port", "1234"]) == 0
        admin.assert_called_once_with("127.0.0.1", 1234)
    children = [Mock(), Mock()]
    children[0].poll.return_value = 2
    children[0].returncode = 2
    children[1].poll.return_value = None
    with patch("maidanlao.cli._start_child", side_effect=children) as start:
        assert run_all() == 2
        assert start.call_count == 2
        assert all("reminder" not in str(call) for call in start.call_args_list)
    children[1].terminate.assert_called_once()


def test_qq_paging_and_private_group_send_failure():
    from maidanlao.adapters.napcat import NapCatBot, NapCatError, NapCatSettings

    bot = NapCatBot(NapCatSettings("http://localhost", "ws://localhost"), ":memory:")
    bot.client = Mock()

    async def scenario():
        assert await bot._send_response(AssistantResponse("文" * 4500), group_id="2", reply_to="3")
        assert await bot._send_response(AssistantResponse("私聊"), user_id="1")
        bot.client.send_private.side_effect = NapCatError()
        assert not await bot._send_response(AssistantResponse("失败"), user_id="1")

    asyncio.run(scenario())
    calls = bot.client.send_group.call_args_list
    assert len(calls) == 3
    assert calls[0].args[1][0] == {"type": "reply", "data": {"id": "3"}}
    assert len(calls[1].args[1]) == 2000 and len(calls[2].args[1]) == 500


def test_napcat_http_error_response_contract():
    from maidanlao.adapters.napcat import NapCatClient, NapCatError, NapCatSettings

    client = NapCatClient(NapCatSettings("http://localhost", "ws://localhost"))
    for value in [[], {}, {"status": "ok", "retcode": "bad"}, {"status": "ok", "retcode": None}]:
        with pytest.raises(NapCatError):
            client._validate_response(value, "test")
    for error in [URLError("down"), TimeoutError(), HTTPError("url", 500, "failed", {}, BytesIO(b"failed"))]:
        with patch("maidanlao.adapters.napcat.client.urlopen", side_effect=error), pytest.raises(NapCatError):
            client.call("test")
    with patch.object(client, "call", side_effect=NapCatError()):
        assert not client.send_private_text("1", "text")
        assert not client.send_group_text("2", "text")


def test_same_qq_conversation_is_serial_other_users_are_isolated():
    from maidanlao.adapters.napcat import NapCatBot, NapCatSettings

    bot = NapCatBot(NapCatSettings("http://localhost", "ws://localhost"), ":memory:")
    active = 0
    maximum = 0

    async def process(_):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.01)
        active -= 1

    bot._process_message = process

    def event(id, user=1):
        return dict(
            post_type="message",
            message_type="private",
            message_id=id,
            user_id=user,
            self_id=999,
            message="hi",
        )

    async def scenario():
        await asyncio.gather(bot.handle_event(event(1)), bot.handle_event(event(2)))
        assert maximum == 1
        await asyncio.gather(bot.handle_event(event(3)), bot.handle_event(event(4, user=3)))
        assert len(bot._conversation_locks) == 2

    asyncio.run(scenario())
