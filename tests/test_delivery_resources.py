"""交付模板不索引，按主题或明确请求发送原始字节。"""

import json
from contextlib import closing
from unittest.mock import Mock, patch

import pytest
from langchain_core.messages import AIMessage
from test_agentic_rag import ScriptedModel, file_model, tool_call
from test_original_delivery import FIELDS

from maidanlao.cli import main
from maidanlao.persistence.database import open_database
from maidanlao.persistence.repository import KnowledgeRepository
from maidanlao.rag import KnowledgeIngestService, LocalHashEmbeddingClient
from maidanlao.service import AssistantService, conversation_key


def register(repo, resource_id="res_opening_2027", **overrides):
    fields = dict(
        resource_id=resource_id,
        title="2027届开题报告模板",
        source="用户提供的模板",
        filename="开题报告样式2027.docx",
        content=b"original-template",
        keywords=["开题报告", "开题模板", "开题"],
        description="供开题报告填写使用。",
        cohort="2027",
    )
    return repo.resources.register(**{**fields, **overrides})


@pytest.fixture
def resource_db(tmp_path):
    path = str(tmp_path / "resource.db")
    with closing(open_database(path)) as db:
        register(KnowledgeRepository(db))
    return path


@pytest.mark.parametrize("question", ["请发给我开题报告模板", "把《2027届开题报告模板》的原文件发给我"])
def test_agent_prepares_requested_template_without_vectors(resource_db, question):
    app = AssistantService(resource_db, file_model("res_opening_2027"), Mock(), None)
    response = app.handle_message("1", None, "private", question)
    assert response.attachments[0].content == b"original-template"
    assert app.model.seen
    app.embedding_client.embed.assert_not_called()
    with closing(open_database(resource_db)) as db:
        assert not KnowledgeRepository(db).chat.recent(conversation_key("1", None, "private"))
    app.commit_turn(response)
    with closing(open_database(resource_db)) as db:
        assert KnowledgeRepository(db).knowledge_documents.list_all() == []
        assert db.session.connection().exec_driver_sql("SELECT COUNT(*) FROM knowledge_chunks").fetchone()[0] == 0
        assert (
            db.session.connection().exec_driver_sql("SELECT COUNT(*) FROM knowledge_vector_indexes").fetchone()[0] == 0
        )
        turn = KnowledgeRepository(db).chat.recent(conversation_key("1", None, "private"))[-1]
        assert json.loads(turn.sources_json)[0]["resource_id"] == "res_opening_2027"


def test_followup_is_isolated_and_preserves_raw_file(resource_db):
    app = AssistantService(resource_db, file_model("res_opening_2027"), Mock(), None)
    response = app.handle_message("1", None, "private", "发给我开题报告模板")
    app.commit_turn(response)
    restarted = AssistantService(resource_db, file_model("res_opening_2027"), Mock(), None)
    assert restarted.handle_message("1", None, "private", "再发一次这个模板").attachments
    assert not restarted.handle_message("2", None, "private", "再发一次这个模板").attachments
    assert not restarted.handle_message("1", "2", "group", "再发一次这个模板").attachments


@pytest.mark.parametrize(
    "question", ["2026届开题报告怎么写", "补考报名时间", "不用发文件，开题报告怎么写", "请发《不存在的模板》"]
)
def test_unrelated_wrong_cohort_optout_and_unknown_titles_do_not_deliver(resource_db, question):
    app = AssistantService(resource_db, ScriptedModel(script=[AIMessage(content="没有依据")]), Mock(), None)
    assert not app.handle_message("1", None, "private", question).attachments


def test_registration_idempotence_integrity_and_inactive(resource_db):
    with closing(open_database(resource_db)) as db:
        repo = KnowledgeRepository(db)
        register(repo)
        assert len(repo.resources.list_all()) == 1
        assert "content" not in repo.resources.list_all()[0]
        with pytest.raises(ValueError, match="already_registered"):
            register(repo, content=b"replacement")
        db.session.connection().exec_driver_sql("UPDATE delivery_resources SET sha256 = 'bad'")
        db.session.commit()
    app = AssistantService(resource_db, file_model("res_opening_2027"), Mock(), None)
    response = app.handle_message("1", None, "private", "发给我开题模板")
    assert "校验失败" in response.text and not response.attachments
    with closing(open_database(resource_db)) as db:
        db.session.connection().exec_driver_sql("UPDATE delivery_resources SET status = 'inactive'")
        db.session.commit()
    assert not app.handle_message("1", None, "private", "发给我开题模板").attachments


def test_only_on_request_multiple_templates_and_policy_answer(resource_db):
    with closing(open_database(resource_db)) as db:
        repo = KnowledgeRepository(db)
        register(repo, "second", title="开题附件", filename="附件.doc", auto_send=False)
        KnowledgeIngestService(repo, LocalHashEmbeddingClient()).ingest_text(**FIELDS)
    model = ScriptedModel(script=[tool_call("学位申请条件"), AIMessage(content="需要满足第八条条件。")])
    app = AssistantService(resource_db, model, LocalHashEmbeddingClient(), None)
    response = app.handle_message("1", None, "private", "开题报告与学位申请条件有什么关系？")
    assert "第八条" in response.text
    assert len(response.attachments) == 0  # 讨论模板不再自动发送附件。
    assert any(s.get("document_id") for s in response.sources)
    assert not any(s.get("resource_id") for s in response.sources)
    app.model = file_model("res_opening_2027")
    ambiguous = app.handle_message("1", None, "private", "请发给我开题模板")
    assert "多份" in ambiguous.text and not ambiguous.attachments
    app.model = file_model("second")
    named = app.handle_message("1", None, "private", "请发《开题附件》")
    assert named.attachments[0].filename == "附件.doc"


@pytest.mark.parametrize("overrides", [{"resource_id": "../bad"}, {"keywords": []}, {"keywords": [""]}, {"title": ""}])
def test_registration_rejects_invalid_metadata(resource_db, overrides):
    with closing(open_database(resource_db)) as db, pytest.raises(ValueError):
        register(KnowledgeRepository(db), **overrides)


def test_cli_register_and_list_without_constructing_embedding(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", str(tmp_path / "cli.db"))
    file = tmp_path / "推荐表.doc"
    file.write_bytes(b"unchanged-doc")
    with patch("maidanlao.cli.LangChainEmbeddingClient", side_effect=AssertionError("must not embed")):
        assert (
            main(
                [
                    "import-resource",
                    "--resource-id",
                    "res_recommendation_2027",
                    "--file",
                    str(file),
                    "--title",
                    "优秀毕业论文推荐表",
                    "--source",
                    "提供的模板",
                    "--keyword",
                    "优秀毕业论文",
                    "--cohort",
                    "2027",
                    "--description",
                    "推荐意见须手写。",
                ]
            )
            == 0
        )
        assert json.loads(capsys.readouterr().out)["resource_id"] == "res_recommendation_2027"
        assert main(["list-resources"]) == 0
        assert json.loads(capsys.readouterr().out)[0]["description"] == "推荐意见须手写。"
