"""原件与清洗正文的关联、事务、会话定位和 QQ 交付契约。"""

import asyncio
import base64
import json
from contextlib import closing
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from langchain_core.messages import AIMessage
from test_agentic_rag import ScriptedModel, file_model, tool_call
from test_interfaces import backend, request

from maidanlao.adapters.napcat import NapCatBot, NapCatClient, NapCatError, NapCatSettings
from maidanlao.api.server import parse_import
from maidanlao.cli import main
from maidanlao.persistence.database import initialize_database, open_database
from maidanlao.persistence.repository import KnowledgeRepository
from maidanlao.rag import KnowledgeIngestService, LocalHashEmbeddingClient
from maidanlao.rag.models import OriginalAttachment
from maidanlao.rag.originals import MAX_ORIGINAL_BYTES, validate_original
from maidanlao.service import AssistantResponse, AssistantService, conversation_key

ORIGINAL = b"%PDF-1.7\r\n\x00\xff School original\r\n%%EOF"
FIELDS = {
    "document_id": "doc_degree_rules_2026",
    "title": "学士学位授予规定",
    "source": "学校",
    "publish_date": "2026-06-15",
    "scope": "school",
    "category": "毕业资格",
    "raw_text": "第八条 申请学士学位须满足学位授予条件。",
    "created_by": 0,
}


def ingest(repo, **overrides):
    return KnowledgeIngestService(repo, LocalHashEmbeddingClient()).ingest_text(
        **{**FIELDS, "original_name": "学士学位授予规定.pdf", "original_content": ORIGINAL, **overrides}
    )


def multipart(fields=None, original=ORIGINAL, filename="学士学位授予规定.pdf"):
    parts = [
        f'--boundary\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
        for key, value in (fields or {}).items()
    ]
    parts.append(
        f'--boundary\r\nContent-Disposition: form-data; name="original_file"; filename="{filename}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n".encode()
        + original
        + b"\r\n--boundary--\r\n"
    )
    return b"".join(parts)


def test_original_is_byte_identical_separate_from_chunks_and_preserved_on_rebuild(tmp_path):
    with closing(open_database(str(tmp_path / "db"))) as db:
        repo = KnowledgeRepository(db)
        doc_id = ingest(repo)
        original = repo.knowledge_documents.original(doc_id)
        assert original.content == ORIGINAL
        assert original.sha256 == validate_original(original.filename, ORIGINAL)
        assert all("School original" not in c["chunk_text"] for c in repo.knowledge_chunks.all_with_documents())
        assert repo.knowledge_documents.list_all()[0]["has_original"]
        KnowledgeIngestService(repo, LocalHashEmbeddingClient()).reingest_document(doc_id)
        assert repo.knowledge_documents.original(doc_id).content == ORIGINAL
        repo.knowledge_documents.attach_original(doc_id, original.filename, ORIGINAL)
        with pytest.raises(ValueError, match="already_attached"):
            repo.knowledge_documents.attach_original(doc_id, original.filename, b"replacement")
        with pytest.raises(ValueError, match="document_not_found"):
            repo.knowledge_documents.attach_original("missing", original.filename, ORIGINAL)


def test_original_rolls_back_with_failed_embedding_and_legacy_database_upgrades(tmp_path):
    path = str(tmp_path / "db")
    with closing(open_database(path)) as db:
        db.session.connection().exec_driver_sql("DROP TABLE knowledge_originals")
        db.session.commit()
    initialize_database(path, force=True)
    with closing(open_database(path)) as db:
        repo = KnowledgeRepository(db)
        broken = Mock(model_name="broken")
        broken.embed.side_effect = RuntimeError("offline")
        with pytest.raises(RuntimeError):
            KnowledgeIngestService(repo, broken).ingest_text(
                **FIELDS, original_name="rules.pdf", original_content=ORIGINAL
            )
        assert repo.knowledge_documents.get(FIELDS["document_id"]) is None
        assert repo.knowledge_documents.original(FIELDS["document_id"]) is None
        with pytest.raises(ValueError, match="incomplete_original"):
            KnowledgeIngestService(repo, broken).ingest_text(**FIELDS, original_name="rules.pdf")
        with pytest.raises(ValueError, match="invalid_document_id"):
            ingest(repo, document_id="../bad")


@pytest.mark.parametrize("name", ["../rules.pdf", "x\\rules.docx", "x\x00.pdf", "rules.exe", "", None])
def test_original_filename_rejects_paths_and_unsupported_files(name):
    with pytest.raises(ValueError, match="filename"):
        validate_original(name, ORIGINAL)


@pytest.mark.parametrize(
    "content", [b"", "not bytes", b"x" * (MAX_ORIGINAL_BYTES + 1)], ids=["empty", "text", "oversize"]
)
def test_original_size_and_binary_validation(content):
    with pytest.raises(ValueError, match="size"):
        validate_original("rules.pdf", content)


def test_followup_after_restart_uses_file_tool_without_embedding(tmp_path):
    path = str(tmp_path / "db")
    with closing(open_database(path)) as db:
        ingest(KnowledgeRepository(db))
    model = ScriptedModel(script=[tool_call("学位申请条件"), AIMessage(content="根据规定，需要满足第八条条件。")])
    app = AssistantService(path, model, LocalHashEmbeddingClient(), None)
    answer = app.handle_message("1", None, "private", "申请学位需要满足哪些条件？")
    assert answer.sources[0]["document_id"] == FIELDS["document_id"]
    assert answer.sources[0]["source"] == "学校"
    assert "第八条" in answer.text
    restarted = AssistantService(path, file_model(FIELDS["document_id"]), Mock(), None)
    result = restarted.handle_message("1", None, "private", "把这个规定的原文件发给我。")
    assert result.attachments == [OriginalAttachment(FIELDS["document_id"], "学士学位授予规定.pdf", ORIGINAL)]
    assert restarted.model.seen
    restarted.embedding_client.embed.assert_not_called()
    restarted.commit_turn(result)  # 模拟入口收到接口确认后提交本轮。
    assert not restarted.handle_message("2", None, "private", "把这个规定的原文件发给我。").attachments
    assert not restarted.handle_message("1", "3", "group", "把这个规定的原文件发给我。").attachments
    assert restarted.handle_message("1", None, "private", "再发一次原文件").attachments
    assert not restarted.handle_message("1", None, "private", "发给我《不存在的规定》的原文件").attachments
    assert not restarted.handle_message("1", None, "private", "发给我 doc_unknown 的原文件").attachments
    with closing(open_database(path)) as db:
        KnowledgeIngestService(KnowledgeRepository(db), LocalHashEmbeddingClient()).reingest_document(
            FIELDS["document_id"]
        )
    stale = restarted.handle_message("1", None, "private", "把原文件发给我")
    assert not stale.attachments and "版本已变化" in stale.text


def test_ambiguity_missing_original_named_document_and_integrity(tmp_path):
    path = str(tmp_path / "db")
    with closing(open_database(path)) as db:
        repo = KnowledgeRepository(db)
        first = ingest(repo)
        second = ingest(repo, document_id="second", title="补充规定", original_name=None, original_content=None)
        sources = [{"document_id": doc_id, "document_version": 1} for doc_id in (first, second)]
        repo.chat.append(conversation_key("1", None, "private"), "条件？", "条件", sources)
    app = AssistantService(path, file_model(first), Mock(), None)
    assert "多份文档" in app.handle_message("1", None, "private", "把原文件发给我").text
    app.model = file_model(second)
    assert "尚未关联" in app.handle_message("1", None, "private", "发给我补充规定的原文件").text
    app.model = file_model(first)
    assert app.handle_message("2", None, "private", "发给我《学士学位授予规定》的原文件").attachments
    assert app.handle_message("2", None, "private", "发给我《学士学位授予规定.pdf》").attachments
    with closing(open_database(path)) as db:
        db.session.connection().exec_driver_sql(
            "UPDATE knowledge_originals SET content = ? WHERE document_id = ?", (b"tampered", first)
        )
        db.session.commit()
    assert "完整性" in app.handle_message("1", None, "private", "发给我学士学位授予规定的原文件").text
    with closing(open_database(path)) as db:
        db.session.connection().exec_driver_sql(
            "UPDATE knowledge_documents SET status = 'inactive' WHERE id = ?", (first,)
        )
        db.session.commit()
    inactive = app.handle_message("1", None, "private", "发给我学士学位授予规定的原文件")
    assert "没有找到" in inactive.text and not inactive.attachments


def test_admin_binary_import_download_auth_attach_and_rebuild(tmp_path):
    raw = multipart({k: v for k, v in FIELDS.items() if k != "created_by"})
    assert parse_import(raw, "multipart/form-data; boundary=boundary")["original_content"] == ORIGINAL
    with backend(tmp_path) as base:
        req = Request(
            base + "/admin/documents/import",
            raw,
            headers={"Authorization": "Bearer test", "Content-Type": "multipart/form-data; boundary=boundary"},
        )
        with urlopen(req) as response:
            doc_id = json.loads(response.read())["document_id"]
        assert doc_id == FIELDS["document_id"]
        assert "下载原件" in request(base, "/admin")[1]
        download = base + f"/admin/documents/{doc_id}/original"
        with pytest.raises(HTTPError) as error:
            urlopen(download)
        assert error.value.code == 401
        with urlopen(Request(download, headers={"Authorization": "Bearer test"})) as response:
            assert response.read() == ORIGINAL
            assert "filename*=UTF-8" in response.headers["Content-Disposition"]
        assert request(base, f"/admin/documents/{doc_id}/reindex", {})[0] == 200
        fields = {**FIELDS, "document_id": "other"}
        assert request(base, "/admin/documents/import", fields)[0] == 201
        attach = Request(
            base + "/admin/documents/other/original",
            multipart(),
            headers={"Authorization": "Bearer test", "Content-Type": "multipart/form-data; boundary=boundary"},
        )
        with urlopen(attach) as response:
            assert response.status == 200
        with urlopen(attach) as response:
            assert response.status == 200  # 重试相同原件幂等
        assert request(base, "/admin/documents/missing/original", {})[0] == 404
        assert request(base, "/admin/documents/other/original", {})[0] == 400
        assert request(base, "/admin/documents/missing/original")[0] == 404
        with closing(open_database(str(tmp_path / "http.db"))) as db:
            db.session.connection().exec_driver_sql(
                "UPDATE knowledge_originals SET filename = 'bad.exe' WHERE document_id = ?", (doc_id,)
            )
            db.session.commit()
        assert request(base, f"/admin/documents/{doc_id}/original")[0] == 409


def test_qq_file_transport_byte_identity_target_and_failure():
    client = NapCatClient(NapCatSettings("http://localhost", "ws://localhost"))
    with patch.object(client, "call") as call:
        client.send_file("rules.pdf", ORIGINAL, user_id="1")
        action, payload = call.call_args.args
        assert action == "upload_private_file" and payload["user_id"] == "1"
        assert base64.b64decode(payload["file"].removeprefix("base64://")) == ORIGINAL
        client.send_file("rules.pdf", ORIGINAL, group_id="2")
        assert call.call_args.args[0] == "upload_group_file"
        assert call.call_args.args[1]["group_id"] == "2"
        with pytest.raises(ValueError):
            client.send_file("rules.pdf", ORIGINAL)
    bot = NapCatBot(client.settings, ":memory:")
    bot.client = Mock()
    response = AssistantResponse("已定位原件", [], [OriginalAttachment("doc", "rules.pdf", ORIGINAL)])
    assert asyncio.run(bot._send_response(response, group_id="2"))
    bot.client.send_file.assert_called_once_with("rules.pdf", ORIGINAL, user_id=None, group_id="2")
    assert asyncio.run(bot._send_response(response, user_id="1"))
    bot.client.send_file.side_effect = NapCatError()
    assert not asyncio.run(bot._send_response(response, user_id="1"))
    assert "失败" in bot.client.send_private.call_args.args[1]
    assert not asyncio.run(bot._send_response(response, group_id="2"))
    bot.client.send_group.side_effect = NapCatError()
    assert not asyncio.run(bot._send_response(response, group_id="2"))


def test_cli_import_pair_and_attach_to_existing(tmp_path, monkeypatch, capsys):
    path = str(tmp_path / "cli.db")
    monkeypatch.setenv("DATABASE_URL", path)
    monkeypatch.setattr("maidanlao.cli.LangChainEmbeddingClient", LocalHashEmbeddingClient)
    md = tmp_path / "rules.md"
    md.write_text(FIELDS["raw_text"], encoding="utf-8")
    original = tmp_path / "rules.pdf"
    original.write_bytes(ORIGINAL)
    args = [
        "import-doc",
        "--file",
        str(md),
        "--document-id",
        FIELDS["document_id"],
        "--title",
        FIELDS["title"],
        "--source",
        "学校",
        "--publish-date",
        FIELDS["publish_date"],
        "--category",
        "毕业资格",
        "--original-file",
        str(original),
    ]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["document_id"] == FIELDS["document_id"]
    assert main(["attach-original", "--document-id", FIELDS["document_id"], "--file", str(original)]) == 0
    with closing(open_database(path)) as db:
        assert KnowledgeRepository(db).knowledge_documents.original(FIELDS["document_id"]).content == ORIGINAL
