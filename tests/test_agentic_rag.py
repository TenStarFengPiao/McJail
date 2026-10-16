"""通过真实 create_agent 和 SQLAlchemy 验证问答、安全失败与历史隔离。"""

import json
from contextlib import closing
from typing import Any
from unittest.mock import patch

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from maidanlao.persistence.database import open_database
from maidanlao.persistence.repository import KnowledgeRepository
from maidanlao.rag import KnowledgeIngestService, LocalHashEmbeddingClient
from maidanlao.service import AssistantService, conversation_key


class ScriptedModel(BaseChatModel):
    """按脚本返回工具调用或回答，同时记录框架真实传入的历史。"""

    script: list[Any]
    seen: list = Field(default_factory=list)
    bindings: list = Field(default_factory=list)
    cursor: int = 0

    @property
    def _llm_type(self):
        return "offline-script"

    def bind_tools(self, tools, **kwargs):
        self.bindings.append((tools, kwargs))
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(messages)
        value = self.script[min(self.cursor, len(self.script) - 1)]
        self.cursor += 1
        if isinstance(value, Exception):
            raise value
        # 模拟真实提供方：每次模型响应的工具调用 ID 都唯一。
        value = value.model_copy(
            update={"tool_calls": [{**call, "id": f"{self.cursor}-{call['id']}"} for call in value.tool_calls]}
        )
        return ChatResult(generations=[ChatGeneration(message=value)])


def tool_call(query="补考报名", count=1):
    return AIMessage(
        content="",
        tool_calls=[
            {"name": "search_knowledge", "args": {"query": query}, "id": f"call-{i}", "type": "tool_call"}
            for i in range(count)
        ],
    )


def file_model(document_id):
    """每个请求模拟 Agent 选择文件工具，随后生成文本；不模拟文件发送。"""

    class FileModel(ScriptedModel):
        def _generate(self, messages, *args, **kwargs):
            self.cursor = 0 if messages[-1].type == "human" else 1
            return super()._generate(messages, *args, **kwargs)

    call = AIMessage(
        content="",
        tool_calls=[
            {"name": "get_document_file", "args": {"document_id": document_id}, "id": "file", "type": "tool_call"}
        ],
    )
    return FileModel(script=[call, AIMessage(content="我来准备文件。")])


@pytest.fixture
def knowledge(tmp_path):
    path = str(tmp_path / "test.sqlite3")
    with closing(open_database(path)) as db:
        KnowledgeIngestService(KnowledgeRepository(db), LocalHashEmbeddingClient()).ingest_text(
            title="补考通知",
            source="教学办",
            publish_date="2026-09-10",
            scope="school",
            category="补考通知",
            raw_text="补考报名截止到周五。",
            created_by=0,
        )
    return path


def service(path, script):
    model = ScriptedModel(script=script)
    return AssistantService(path, model, LocalHashEmbeddingClient(), None), model


def history(path, user="1", group=None, source="private"):
    with closing(open_database(path)) as db:
        return [
            (t.question, t.answer, json.loads(t.sources_json))
            for t in KnowledgeRepository(db).chat.recent(conversation_key(user, group, source), limit=100)
        ]


def test_agent_direct_social_response(knowledge):
    app, model = service(knowledge, [AIMessage(content="你好，我可以帮你查知识文档。")])
    response = app.handle_message("1", None, "private", "你好")
    assert response.text.startswith("你好")
    assert response.sources == []
    assert len(history(knowledge)) == 1
    tools, settings = model.bindings[0]
    assert [t.name for t in tools] == ["search_knowledge", "get_document_file", "get_current_time", "get_weather"]
    assert settings["parallel_tool_calls"] is False


def test_real_agent_search_retry_artifact_and_history(knowledge):
    app, model = service(
        knowledge, [tool_call(), tool_call("报名截止时间"), AIMessage(content="根据补考通知，周五截止。")]
    )
    response = app.handle_message("1", None, "private", "补考什么时候报名？")
    assert "周五" in response.text
    assert "参考来源：" in response.text
    assert len(response.sources) == 1
    assert history(knowledge)[0][2] == response.sources
    tool_messages = [m for m in model.seen[-1] if m.type == "tool"]
    assert len(tool_messages) == 2
    assert tool_messages[0].artifact["sources"][0]["source_title"] == "补考通知"
    assert "chunk_text" not in tool_messages[0].artifact["sources"][0]


def test_followup_after_restart_and_eight_complete_turns(knowledge):
    with closing(open_database(knowledge)) as db:
        repo = KnowledgeRepository(db).chat
        for i in range(10):
            repo.append(conversation_key("1", None, "private"), f"问题{i}", f"答复{i}", [])
    app, model = service(knowledge, [tool_call("补考截止时间"), AIMessage(content="周五截止。")])
    app.handle_message("1", None, "private", "那什么时候截止？")
    input_history = model.seen[0]
    assert len(input_history) == 18  # 系统 + 最近八轮 + 本轮问题
    assert input_history[1].content == "问题2"
    assert input_history[-1].content == "那什么时候截止？"
    assert len(history(knowledge)) == 11


@pytest.mark.parametrize(
    "user,group,source", [("2", None, "private"), ("1", "1", "group"), ("1", "2", "group"), ("2", "1", "group")]
)
def test_conversation_isolation(knowledge, user, group, source):
    app, _ = service(knowledge, [AIMessage(content="你好")])
    app.handle_message("1", None, "private", "你好")
    other, model = service(knowledge, [AIMessage(content="你好")])
    other.handle_message(user, group, source, "你好")
    assert len(model.seen[0]) == 2
    assert len(history(knowledge, user, group, source)) == 1


@pytest.mark.parametrize(
    "script,text,expected",
    [
        ([RuntimeError("model down")], "你好", "处理失败"),
        ([AIMessage(content="")], "你好", "有效答复"),
        ([tool_call(count=9)], "补考政策", "调用上限"),
        ([tool_call()], "补考政策", "调用上限"),
    ],
)
def test_failures_never_save_partial_turn(knowledge, script, text, expected):
    app, _ = service(knowledge, script)
    response = app.handle_message("1", None, "private", text)
    assert expected in response.text
    assert history(knowledge) == []


def test_no_evidence_is_explicit(tmp_path):
    path = str(tmp_path / "empty.db")
    app, _ = service(path, [tool_call(), AIMessage(content="没查到补考规定，暂时无法确定。")])
    response = app.handle_message("1", None, "private", "补考政策")
    assert response.text == "没查到补考规定，暂时无法确定。"
    assert response.sources == []
    assert len(history(path)) == 1


@pytest.mark.parametrize("text", ["你说的话要缩短一些，不要长篇大论", "你好呀", "改成一句话", "什么是向量？"])
def test_direct_answers_do_not_require_social_whitelist_or_sources(knowledge, text):
    app, model = service(knowledge, [AIMessage(content="好的，我简短些。")])
    response = app.handle_message("1", None, "private", text)
    assert response.text == "好的，我简短些。"
    assert response.sources == []
    assert not any(message.type == "tool" for message in model.seen[-1])
    assert history(knowledge)[0][0] == text


def test_style_preference_reaches_followup_history(knowledge):
    app, _ = service(knowledge, [AIMessage(content="好，我简短些。")])
    app.handle_message("1", None, "private", "以后简短回答")
    next_app, model = service(knowledge, [AIMessage(content="你好呀。")])
    next_app.handle_message("1", None, "private", "你好呀")
    assert model.seen[0][1].content == "以后简短回答"
    assert model.seen[0][2].content.split("\n来源元数据：", 1)[0] == "好，我简短些。"


def test_retrieval_exception_is_explicit(knowledge, monkeypatch):
    monkeypatch.setattr("maidanlao.service.HybridRetriever.search", lambda *_: (_ for _ in ()).throw(OSError()))
    app, _ = service(knowledge, [tool_call(), AIMessage(content="周五")])
    assert "检索暂时不可用" in app.handle_message("1", None, "private", "补考报名").text
    assert history(knowledge) == []


def test_empty_and_invalid_identity(knowledge):
    app, model = service(knowledge, [AIMessage(content="你好")])
    assert "请输入" in app.handle_message("1", None, "private", " ").text
    assert not model.seen
    for identity in [("", None, "private"), ("1", None, "group"), ("1", None, "unknown")]:
        with pytest.raises(ValueError):
            conversation_key(*identity)


def test_ninth_call_is_blocked_and_eight_calls_can_complete(knowledge):
    from maidanlao.service import HybridRetriever

    original = HybridRetriever.search
    calls = []

    def counted(self, *args, **kwargs):
        calls.append(args[0])
        return original(self, *args, **kwargs)

    with patch.object(HybridRetriever, "search", counted):
        app, _ = service(knowledge, [tool_call()] * 8 + [AIMessage(content="补考通知：周五。")])
        assert app.handle_message("1", None, "private", "补考").sources
        assert len(calls) == 8
        calls.clear()
        app, _ = service(knowledge, [tool_call()] * 9 + [AIMessage(content="周五。")])
        assert "调用上限" in app.handle_message("2", None, "private", "补考").text
        assert len(calls) == 8
        assert history(knowledge, "2") == []


def test_empty_search_can_retry_and_existing_history_survives_failure(knowledge):
    from maidanlao.rag.models import RagResult
    from maidanlao.service import HybridRetriever

    original = HybridRetriever.search
    calls = []

    def first_empty(self, *args, **kwargs):
        calls.append(args[0])
        return RagResult("insufficient_evidence", 0, [], "", ()) if len(calls) == 1 else original(self, *args, **kwargs)

    with patch.object(HybridRetriever, "search", first_empty):
        app, _ = service(knowledge, [tool_call("没有结果的问法"), tool_call(), AIMessage(content="周五。")])
        assert app.handle_message("1", None, "private", "补考").sources
    before = history(knowledge)
    failing, _ = service(knowledge, [RuntimeError()])
    failing.handle_message("1", None, "private", "追问")
    assert history(knowledge) == before


def test_multiple_tool_calls_are_serial_in_framework(knowledge):
    import threading
    import time

    from maidanlao.service import HybridRetriever

    original = HybridRetriever.search
    guard = threading.Lock()
    active = 0
    maximum = 0

    def counted(self, *args, **kwargs):
        nonlocal active, maximum
        with guard:
            active += 1
            maximum = max(maximum, active)
        try:
            time.sleep(0.01)
            return original(self, *args, **kwargs)
        finally:
            with guard:
                active -= 1

    with patch.object(HybridRetriever, "search", counted):
        app, _ = service(knowledge, [tool_call(count=3), AIMessage(content="周五。")])
        assert app.handle_message("1", None, "private", "补考").sources
    assert maximum == 1
