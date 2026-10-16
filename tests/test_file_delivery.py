"""文件语义工具、保守定位、交付回执与单次来源输出的端到端回归。"""

import asyncio
import json
import sqlite3
from contextlib import closing
from unittest.mock import Mock, patch

import pytest
from langchain_core.messages import AIMessage
from test_agentic_rag import ScriptedModel, file_model, tool_call
from test_original_delivery import FIELDS, ORIGINAL, ingest

from maidanlao.adapters.napcat import NapCatBot, NapCatError, NapCatSettings
from maidanlao.adapters.napcat.client import NapCatUnknownError
from maidanlao.persistence.database import initialize_database, open_database
from maidanlao.persistence.delivery import DeliveryLedger
from maidanlao.persistence.repository import KnowledgeRepository
from maidanlao.rag import LocalHashEmbeddingClient
from maidanlao.rag.models import OriginalAttachment
from maidanlao.service import AssistantResponse, AssistantService, PendingTurn, conversation_key
from maidanlao.text import with_references


@pytest.fixture
def file_db(tmp_path):
    path = str(tmp_path / "files.db")
    with closing(open_database(path)) as db:
        repo = KnowledgeRepository(db)
        ingest(repo)
        repo.knowledge_documents.set_aliases(FIELDS["document_id"], ["学位规定"])
    return path


def app(path, model=None):
    return AssistantService(path, model or file_model(FIELDS["document_id"]), LocalHashEmbeddingClient(), None)


def turns(path):
    with closing(open_database(path)) as db:
        return KnowledgeRepository(db).chat.recent(conversation_key("1", None, "private"))


@pytest.mark.parametrize(
    "question", ["不要发学位规定原文件", "不用提供学位规定附件", "别发学位规定", "不要发原件，但又想让你发学位规定"]
)
def test_denial_blocks_even_a_model_that_calls_file_tool(file_db, question):
    response = app(file_db).prepare_response("1", None, "private", question)
    assert "不会发送" in response.text and not response.attachments
    assert turns(file_db) == []


def test_alias_and_missing_name_never_fall_back_to_history(file_db):
    service = app(file_db)
    response = service.prepare_response("1", None, "private", "学位规定原件发我一下", "alias-request")
    assert response.attachments[0].content == ORIGINAL
    assert not turns(file_db)
    service.commit_turn(response)
    for question in ["发给我《不存在的规定》的原件", "发给我其他学院规定的原文件", "发给我授予指南原件"]:
        failed = service.prepare_response("1", None, "private", question)
        assert not failed.attachments and "没有找到" in failed.text
    assert service.prepare_response("1", None, "private", "把刚才那份原件发给我").attachments
    assert not service.prepare_response("2", None, "private", "把刚才那份原件发给我").attachments


def test_discussing_a_document_does_not_auto_send(file_db):
    model = ScriptedModel(script=[tool_call("学位条件"), AIMessage(content="根据第八条，需要满足学位授予条件。")])
    response = app(file_db, model).prepare_response("1", None, "private", "学位规定有哪些要求？")
    assert response.sources and not response.attachments
    assert not turns(file_db)


def prepared(path, request_id="qq-event", two=False):
    attachments = [OriginalAttachment(FIELDS["document_id"], "学士学位授予规定.pdf", ORIGINAL)]
    sources = [{"document_id": FIELDS["document_id"], "document_version": 1, "source_title": FIELDS["title"]}]
    if two:
        with closing(open_database(path)) as db:
            ingest(KnowledgeRepository(db), document_id="doc_second", title="补充规定")
        attachments.append(OriginalAttachment("doc_second", "学士学位授予规定.pdf", ORIGINAL))
        sources.append({"document_id": "doc_second", "document_version": 1, "source_title": "补充规定"})
    return AssistantResponse(
        "已定位原文件。",
        sources,
        attachments,
        PendingTurn(conversation_key("1", None, "private"), "发给我原文件", request_id),
    )


def bot(path):
    instance = NapCatBot(NapCatSettings("http://localhost", "ws://localhost"), path)
    instance.client = Mock()
    return instance


def test_history_commits_only_after_all_items_confirmed_and_retry_skips_files(file_db):
    response = prepared(file_db)
    instance = bot(file_db)
    instance.client.send_private.side_effect = [NapCatError("rejected"), None, None]
    assert not asyncio.run(instance._send_response(response, user_id="1"))
    assert not turns(file_db)
    ledger = DeliveryLedger(file_db, "qq-event")
    assert any(r["status"] == "confirmed" and r["item_key"].startswith("file:") for r in ledger.records())
    assert "接口已确认发送" in instance.client.send_private.call_args.args[1]
    assert asyncio.run(instance._send_response(response, user_id="1"))
    assert instance.client.send_file.call_count == 1
    assert len(turns(file_db)) == 1 and ledger.completed()
    assert asyncio.run(instance._send_response(response, user_id="1"))
    assert instance.client.send_file.call_count == 1 and len(turns(file_db)) == 1


def test_partial_failure_names_confirmed_and_failed_items(file_db):
    response = prepared(file_db, two=True)
    instance = bot(file_db)
    instance.client.send_file.side_effect = [None, NapCatError("action failed"), None]
    assert not asyncio.run(instance._send_response(response, user_id="1"))
    statuses = DeliveryLedger(file_db, "qq-event").records()
    assert len([r for r in statuses if r["status"] == "confirmed"]) == 1
    assert len([r for r in statuses if r["status"] == "failed"]) == 1
    assert not turns(file_db)
    assert asyncio.run(instance._send_response(response, user_id="1"))
    assert instance.client.send_file.call_count == 3 and len(turns(file_db)) == 1


def test_timeout_is_unknown_and_does_not_auto_resend(file_db):
    response = prepared(file_db)
    instance = bot(file_db)

    def timeout(*_args, **_kwargs):
        raise NapCatError("transport") from TimeoutError()

    instance.client.send_file.side_effect = timeout
    assert not asyncio.run(instance._send_response(response, user_id="1"))
    assert "结果未知" in instance.client.send_private.call_args.args[1]
    instance.client.send_file.side_effect = None
    assert not asyncio.run(instance._send_response(response, user_id="1"))
    assert instance.client.send_file.call_count == 1 and not turns(file_db)


def test_invalid_qq_response_is_unknown_without_an_exception_cause(file_db):
    response = prepared(file_db)
    instance = bot(file_db)
    instance.client.send_file.side_effect = NapCatUnknownError("invalid response")
    assert not asyncio.run(instance._send_response(response, user_id="1"))
    assert "结果未知" in instance.client.send_private.call_args.args[1]
    instance.client.send_file.side_effect = None
    assert not asyncio.run(instance._send_response(response, user_id="1"))
    assert instance.client.send_file.call_count == 1 and not turns(file_db)


def test_recovered_response_is_frozen_and_changed_original_cannot_be_sent(file_db):
    response = prepared(file_db)
    ledger = DeliveryLedger(file_db, "qq-event")
    ledger.remember(response)
    response.text = "另一个模型回复"
    ledger.remember(response)
    assert ledger.load_response().text == "已定位原文件。"
    with closing(open_database(file_db)) as db:
        doc = KnowledgeRepository(db).knowledge_documents.get(FIELDS["document_id"])
        doc.version = 2
        db.session.commit()
    with pytest.raises(ValueError, match="document_changed"):
        ledger.load_response()
    assert not asyncio.run(bot(file_db)._send_response(response, user_id="1"))
    assert not turns(file_db)


def test_persisted_sending_claim_is_not_reissued_after_restart(file_db):
    ledger = DeliveryLedger(file_db, "crashed-event")
    assert ledger.claim("file:a", "原件") == "send"
    assert DeliveryLedger(file_db, "crashed-event").claim("file:a", "原件") == "unknown"


def test_history_save_failure_preserves_confirmed_receipts_and_retries_commit(file_db):
    response = prepared(file_db)
    instance = bot(file_db)
    with patch.object(AssistantService, "commit_turn", side_effect=OSError()):
        assert not asyncio.run(instance._send_response(response, user_id="1"))
    assert not turns(file_db)
    assert asyncio.run(instance._send_response(response, user_id="1"))
    assert instance.client.send_file.call_count == 1 and len(turns(file_db)) == 1


@pytest.mark.parametrize(
    "footer", ["参考来源：\n甲文档\n\n参考来源：\n甲文档（甲文档）", "## 参考来源\n- 甲文档", "来源：甲文档"]
)
def test_only_one_canonical_source_footer(footer):
    text = with_references(
        "截止日期为3月20日。\n\n" + footer,
        [{"source_title": "甲文档"}, {"source_title": "甲文档"}, {"source_title": "乙文档"}],
    )
    assert text == "截止日期为3月20日。\n\n参考来源：\n甲文档\n乙文档"


def test_agent_source_footer_is_replaced_by_real_tool_sources(file_db):
    model = ScriptedModel(
        script=[
            tool_call("学位申请"),
            AIMessage(
                content="根据《学士学位授予规定》，需要满足第八条。\n参考来源：\n虚构标题\n\n参考来源：\n学士学位授予规定（学士学位授予规定）"
            ),
        ]
    )
    response = app(file_db, model).prepare_response("1", None, "private", "学位申请条件")
    assert response.text.count("参考来源：") == 1 and "虚构标题" not in response.text
    assert "根据《学士学位授予规定》" in response.text


def test_explicit_two_documents_prepare_two_files_and_bytes_stay_outside_model(file_db):
    with closing(open_database(file_db)) as db:
        ingest(KnowledgeRepository(db), document_id="doc_other", title="补充规定")
    calls = [
        AIMessage(
            content="",
            tool_calls=[
                {"name": "get_document_file", "args": {"document_id": doc_id}, "id": doc_id, "type": "tool_call"}
            ],
        )
        for doc_id in [FIELDS["document_id"], "doc_other"]
    ]
    model = ScriptedModel(script=[*calls, AIMessage(content="已发送两个文件")])
    response = app(file_db, model).prepare_response(
        "1", None, "private", "把《学士学位授予规定》和《补充规定》的原文件都发给我"
    )
    assert len(response.attachments) == 2 and "已发送" not in response.text
    for message in model.seen[-1]:
        if message.type == "tool":
            assert set(message.artifact) == {"sources", "filename"}
            json.dumps(message.artifact)  # 出现二进制字节时无法 JSON 序列化。


def test_wrong_cohort_alias_does_not_select_current_document(file_db):
    with closing(open_database(file_db)) as db:
        doc = KnowledgeRepository(db).knowledge_documents.get(FIELDS["document_id"])
        doc.scope = "2027届"
        db.session.commit()
    response = app(file_db).prepare_response("1", None, "private", "发给我2026届学位规定原件")
    assert not response.attachments and "没有找到" in response.text


def test_upgrade_adds_delivery_fields_without_losing_existing_chat_or_vectors(file_db):
    with closing(open_database(file_db)) as db:
        KnowledgeRepository(db).chat.append(conversation_key("1", None, "private"), "旧问题", "旧答案", [])
        count = db.session.connection().exec_driver_sql("SELECT count(*) FROM knowledge_vector_indexes").scalar()
    with closing(sqlite3.connect(file_db)) as old, old:
        old.execute("ALTER TABLE knowledge_documents DROP COLUMN aliases_json")
        old.execute("DROP INDEX idx_chat_request")
        old.execute("ALTER TABLE chat_turns DROP COLUMN request_id")
        old.execute("ALTER TABLE delivery_attempts DROP COLUMN payload_json")
    initialize_database(file_db, force=True)
    assert turns(file_db)[0].answer == "旧答案"
    with closing(open_database(file_db)) as db:
        assert KnowledgeRepository(db).knowledge_documents.get(FIELDS["document_id"]).aliases_json == "[]"
        assert (
            db.session.connection().exec_driver_sql("SELECT count(*) FROM knowledge_vector_indexes").scalar() == count
        )
    ledger = DeliveryLedger(file_db, "migrated-request")
    ledger.remember(prepared(file_db, "migrated-request"))
    assert ledger.load_response().attachments[0].content == ORIGINAL
