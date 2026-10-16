"""简化后的降级、范围一致性、短事务与 QQ 纯文本回归。"""

from contextlib import closing
from unittest.mock import Mock, patch

import pytest
from langchain_core.messages import AIMessage
from test_agentic_rag import service, tool_call

from maidanlao.agent.tools import SearchSession
from maidanlao.persistence.database import open_database
from maidanlao.persistence.repository import KnowledgeRepository
from maidanlao.rag import HybridRetriever, KnowledgeIngestService, LocalHashEmbeddingClient
from maidanlao.text import plain_text


def add(repo, text="补考报名截止周五。", title="补考通知", **kwargs):
    return KnowledgeIngestService(repo, kwargs.pop("embedder", LocalHashEmbeddingClient())).ingest_text(
        title, "教学办", "2026-10-01", "school", "补考通知", text, 0, **kwargs
    )


@pytest.mark.parametrize("failed_channel", ["vector", "keyword"])
def test_one_channel_failure_still_delivers_evidence(failed_channel):
    with closing(open_database(":memory:")) as db:
        repo = KnowledgeRepository(db)
        add(repo)
        retriever = HybridRetriever(repo, LocalHashEmbeddingClient(), direct_context=False)
        method = "_embedding_search" if failed_channel == "vector" else "_bm25_search"
        with patch.object(retriever, method, side_effect=RuntimeError("offline")):
            search = SearchSession(retriever)
            text, artifact = search.search("补考报名")
        assert "周五" in text and artifact["sources"] and not search.failed


def test_all_channels_failed_is_distinct_from_no_matching_evidence():
    with closing(open_database(":memory:")) as db:
        repo = KnowledgeRepository(db)
        retriever = HybridRetriever(repo, LocalHashEmbeddingClient(), direct_context=False)
        with (
            patch.object(retriever, "_embedding_search", side_effect=RuntimeError()),
            patch.object(retriever, "_bm25_search", side_effect=RuntimeError()),
        ):
            search = SearchSession(retriever)
            assert "暂时不可用" in search.search("补考")[0]
            assert search.failed
        search = SearchSession(retriever)
        assert not search.search("补考")[1]["sources"] and not search.failed


@pytest.mark.parametrize("direct", [True, False])
def test_no_program_filter_or_global_article_rules(direct):
    with closing(open_database(":memory:")) as db:
        repo = KnowledgeRepository(db)
        doc = add(repo, "第十四条 普通本科补考须报名。成人教育补考也须报名。")
        result = HybridRetriever(repo, LocalHashEmbeddingClient(), direct_context=direct).search("普通本科补考")
        assert result.document_ids == (doc,)
        assert "普通本科补考须报名" in result.final_context
        assert "成人教育补考也须报名" in result.final_context


def test_embedding_is_batched_outside_transaction_and_chat_can_write(tmp_path):
    path = str(tmp_path / "batch.db")
    with closing(open_database(path)) as db:
        repo = KnowledgeRepository(db)

        def embed(texts):
            assert not db.session.in_transaction()
            assert len(texts) > 1
            with closing(open_database(path)) as chat_db:
                KnowledgeRepository(chat_db).chat.append("user", "你好", "你好", [])
            return LocalHashEmbeddingClient().embed(texts)

        client = Mock(model_name="batch", embed=Mock(side_effect=embed))
        add(repo, "补考报名截止周五。\n" * 140, embedder=client)
        client.embed.assert_called_once()
        assert len(repo.chat.recent("user")) == 1
        assert repo.knowledge_vectors.count() > 1


def test_concurrent_reindex_does_not_overwrite_newer_document(tmp_path):
    path = str(tmp_path / "versions.db")
    with closing(open_database(path)) as db:
        repo = KnowledgeRepository(db)
        doc_id = add(repo)

        def embed(texts):
            with closing(open_database(path)) as other:
                KnowledgeIngestService(KnowledgeRepository(other), LocalHashEmbeddingClient()).reingest_text(
                    doc_id, "补考通知", "教学办", "2026-10-01", "school", "补考通知", "新版本：周六截止。", 0
                )
            return LocalHashEmbeddingClient().embed(texts)

        client = Mock(model_name="new", embed=Mock(side_effect=embed))
        with pytest.raises(ValueError, match="document_version_changed"):
            KnowledgeIngestService(repo, client).reingest_document(doc_id)
        document = repo.knowledge_documents.get(doc_id)
        assert document.version == 2 and "周六" in document.raw_text


def test_file_engine_initializes_once_with_independent_sessions(tmp_path):
    from maidanlao.persistence import database

    path = str(tmp_path / "engine.db")
    with patch.object(database, "initialize_schema", wraps=database.initialize_schema) as initialize:
        with closing(open_database(path)) as first, closing(open_database(path)) as second:
            assert first.engine is second.engine and first.session is not second.session
        assert initialize.call_count == 1


def test_plain_text_preserves_information_and_is_idempotent():
    original = "# 标题\n**报名条件**与_注意事项_\n- 申请 `doc_degree_2026`\n1. [通知](https://example.com/a_b)\n| 项目 | 日期 |\n| --- | --- |\n| 报名 | 周五 |\n```text\na_b = 2 ** 3\n```"
    converted = plain_text(original)
    assert (
        converted
        == "标题\n报名条件与注意事项\n• 申请 doc_degree_2026\n1）通知（https://example.com/a_b）\n项目；日期\n报名；周五\na_b = 2 ** 3"
    )
    # Plain code content is preserved; applying formatting twice must not alter it.
    assert plain_text(converted) == converted


def test_generated_answer_and_stored_history_are_plain_text(tmp_path):
    path = str(tmp_path / "qq.db")
    with closing(open_database(path)) as db:
        add(KnowledgeRepository(db))
    app, _ = service(path, [tool_call(), AIMessage(content="# 报名\n**周五截止**。\n- 请及时报名。")])
    response = app.handle_message("1", None, "private", "补考报名")
    assert "#" not in response.text and "**" not in response.text and "- " not in response.text
    assert "周五截止" in response.text
    with closing(open_database(path)) as db:
        assert KnowledgeRepository(db).chat.recent('["private", null, "1"]')[0].answer == response.text


def test_database_write_failure_restores_previous_document_and_indexes(tmp_path):
    path = str(tmp_path / "atomic.db")
    with closing(open_database(path)) as db:
        repo = KnowledgeRepository(db)
        doc_id = add(repo)
        before = repo.knowledge_chunks.all_with_documents()
        with patch.object(repo.knowledge_vectors, "upsert", side_effect=RuntimeError("write failed")):
            with pytest.raises(RuntimeError):
                KnowledgeIngestService(repo, LocalHashEmbeddingClient()).reingest_text(
                    doc_id, "补考通知", "教学办", "2026-10-01", "school", "补考通知", "替换后的正文。", 0
                )
        assert repo.knowledge_documents.get(doc_id).version == 1
        assert repo.knowledge_chunks.all_with_documents() == before
    with closing(open_database(path)) as db:
        repo = KnowledgeRepository(db)
        assert repo.knowledge_documents.get(doc_id).raw_text == "补考报名截止周五。"
        assert repo.knowledge_vectors.count() == 1


def test_original_attachment_commits_when_uploaded_separately(tmp_path):
    path = str(tmp_path / "original.db")
    original = b"%PDF-1.7\noriginal\n%%EOF"
    with closing(open_database(path)) as db:
        repo = KnowledgeRepository(db)
        doc_id = add(repo)
        repo.knowledge_documents.attach_original(doc_id, "notice.pdf", original)
    with closing(open_database(path)) as db:
        assert KnowledgeRepository(db).knowledge_documents.original(doc_id).content == original


def test_batch_dimension_mismatch_never_writes_partial_document():
    with closing(open_database(":memory:")) as db:
        repo = KnowledgeRepository(db)
        client = Mock(model_name="broken")
        client.embed.side_effect = lambda texts: [[1.0] if i == 0 else [1.0, 0.0] for i in range(len(texts))]
        with pytest.raises(ValueError, match="embedding_dimension_changed"):
            add(repo, "补考报名截止周五。\n" * 140, embedder=client)
        assert not repo.knowledge_documents.list_all() and repo.knowledge_vectors.count() == 0


def test_final_validation_reads_new_version_after_model_generation(tmp_path):
    from maidanlao.service import AssistantService

    path = str(tmp_path / "validation.db")
    with closing(open_database(path)) as db:
        doc_id = add(KnowledgeRepository(db))

    def build(model, search, files=None, utilities=None):
        def invoke(*args, **kwargs):
            assert search.search("补考报名")[1]["sources"]
            with closing(open_database(path)) as other:
                KnowledgeIngestService(KnowledgeRepository(other), LocalHashEmbeddingClient()).reingest_text(
                    doc_id, "补考通知", "教学办", "2026-10-01", "school", "补考通知", "更新后周六截止。", 0
                )
            return {"messages": [AIMessage(content="周五截止。")]}

        return Mock(invoke=Mock(side_effect=invoke))

    with patch("maidanlao.service.build_agent", side_effect=build):
        app = AssistantService(path, Mock(), LocalHashEmbeddingClient(), None)
        response = app.handle_message("1", None, "private", "补考报名")
    assert "资料已发生变化" in response.text
    with closing(open_database(path)) as db:
        assert not KnowledgeRepository(db).chat.recent('["private", null, "1"]')
