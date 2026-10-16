"""分块、混合检索、上下文预算与离线模型适配器回归。"""

from contextlib import closing
from unittest.mock import patch

import pytest

from maidanlao.config.env import load_env_file
from maidanlao.config.settings import EmbeddingSettings, LLMSettings, database_path
from maidanlao.llm.client import LangChainDeepSeekClient, LangChainEmbeddingClient
from maidanlao.persistence.database import open_database
from maidanlao.persistence.repository import KnowledgeRepository
from maidanlao.rag import (
    DocumentChunker,
    DocumentNormalizer,
    HybridRetriever,
    KnowledgeIngestService,
    LocalHashEmbeddingClient,
    RagEvaluationService,
)
from maidanlao.rag._shared import (
    _cosine,
    _normalize_vector,
    annotate_chunk,
    strip_front_matter,
)
from maidanlao.rag.context import assemble_context
from maidanlao.rag.models import KnowledgeChunk
from maidanlao.rag.regression import RagRegressionService


def test_chunking_long_policy_preserves_text_and_neighbor_integrity():
    raw = "第八条 普通本科学生须满足七项条件之二者。\n" + "竞赛获奖可以满足要求。\n" * 55
    raw += "第十四条 成人教育申请学位要求考试合格。\n" + "继续教育申请材料应提交审核。\n" * 55
    chunks = DocumentChunker().split_text("doc", "section", "学位规定", raw)
    assert len(chunks) >= 3
    assert all(c.chunk_text.startswith("【学位规定") for c in chunks)
    assert chunks[0].prev_chunk_id is None and chunks[-1].next_chunk_id is None
    assert all(
        a.next_chunk_id == b.chunk_id and b.prev_chunk_id == a.chunk_id
        for a, b in zip(chunks, chunks[1:], strict=False)
    )
    inline = DocumentChunker().split_text("inline", "s", "学位规定", "第八条 本科要求。第十四条 成人教育要求。")
    assert len(inline) == 1
    assert "第八条" in inline[0].chunk_text and "第十四条" in inline[0].chunk_text


def test_table_chunks_keep_overlapping_rows_and_markdown_header():
    chunker = DocumentChunker()
    rows = [["姓名", "安排"]] + [[str(i), "周五"] for i in range(35)]
    chunks = chunker.split_table("d", "s", "表格", rows)
    assert len(chunks) == 3
    assert "| 姓名 | 安排 |" in chunks[0].chunk_text
    assert "| 13 | 周五 |" in chunks[0].chunk_text and "| 13 | 周五 |" in chunks[1].chunk_text
    assert len(chunker.split_table("short", "s", "表格", rows[:3])) == 1
    assert chunker._markdown_table([]) == ""


def test_article_anchors_preserve_original_text():
    chunk = KnowledgeChunk("d", "c", 0, "text", "第十四条 本科生考试合格。", 20, "补考规定", "s", "s")
    annotate_chunk(chunk, 120)
    assert "第十四条 本科生考试合格。" in chunk.chunk_text
    assert chunk.chunk_text.startswith("【补考规定｜第十四条】")


def evidence(content, chunk_id="c"):
    return dict(
        content=content,
        chunk_id=chunk_id,
        source_title="补考通知",
        section_path="正文",
        category="补考通知",
        rerank_score=1.0,
        program_type="普通本科",
        scope="school",
        document_version=1,
    )


def test_context_budget_deduplicates_and_keeps_complete_paragraphs():
    first = evidence("短段落。")
    selected, context, omitted = assemble_context([first, dict(first, chunk_id="duplicate")], 500)
    assert len(selected) == 1 and omitted[0]["reason"] == "duplicate"
    assert first["content"] == "短段落。"
    selected, context, omitted = assemble_context([first, evidence("长" * 200, "other")], 250)
    assert len(context) <= 250 and any(o["reason"] == "context_budget" for o in omitted)
    content = "第一段完整。\n" + "超长段落" * 100 + "\n最后一段完整。"
    selected, context, omitted = assemble_context([evidence(content)], 250)
    assert len(context) <= 250 and "第一段完整。" in context and "最后一段完整。" in context
    assert selected[0]["excerpted"] and omitted[0]["reason"] == "paragraph_excerpt"
    selected, _, omitted = assemble_context([evidence("无空行" * 100)], 100)
    assert not selected and omitted[0]["reason"] == "context_budget"


def test_normalization_frontmatter_and_required_metadata():
    normalized = DocumentNormalizer().normalize_text(
        "---\ntitle: old\nsource: school\npublish_date: 2026-10-01\nscope: school\ncategory: 补考通知\n---\n正文",
        {"title": "new"},
    )
    assert normalized.metadata["title"] == "new"
    assert strip_front_matter(normalized.markdown) == "正文"
    assert strip_front_matter("---unfinished") == "---unfinished"
    with pytest.raises(ValueError, match="metadata_missing"):
        DocumentNormalizer().normalize_text("正文")


def test_eval_recall_mrr_missing_fact_and_regression_outcome():
    with closing(open_database(":memory:")) as db:
        repos = KnowledgeRepository(db)
        doc_id = KnowledgeIngestService(repos, LocalHashEmbeddingClient()).ingest_text(
            "补考通知", "教学办", "2026-10-01", "school", "补考通知", "补考报名截止周五。", 0
        )
        chunk_id = repos.knowledge_chunks.all_with_documents()[0]["chunk_id"]
        repos.rag_test_cases.create("补考通知", "补考报名", doc_id, chunk_id, "周五")
        retriever = HybridRetriever(repos, LocalHashEmbeddingClient())
        result = RagEvaluationService(repos, retriever).run()
        assert result["passed"] and result["recall_at_5"] == result["mrr_at_8"] == 1
        repos.rag_test_cases.create("other", "问法", doc_id, "missing", "周六")
        result = RagEvaluationService(repos, retriever).run()
        assert not result["passed"] and len(result["failures"]) == 2
        cases = (
            dict(question="补考报名", intent="policy_explanation", must_appear=["周五"], expected_articles=["第八条"]),
        )
        result = RagRegressionService(repos, retriever, cases).run()
        assert result["all_passed"] and result["avg_answer_rank"] == 1


def test_cloud_client_model_name_and_object_separation(monkeypatch):
    monkeypatch.setenv("EMBEDDING_API_KEY", "fake-key")
    with patch("langchain_openai.OpenAIEmbeddings") as embedding:
        embedding.return_value.embed_documents.return_value = [[0.1, 0.2]]
        client = LangChainEmbeddingClient()
        assert client.model_name == "qwen3.7-text-embedding-flash"
        assert client.client is embedding.return_value
        assert client.embed(["原文"]) == [[0.1, 0.2]]
        assert embedding.call_args.kwargs["model"] == client.model_name
    monkeypatch.setenv("EMBEDDING_API_KEY", "")
    with pytest.raises(RuntimeError, match="api_key_required"):
        LangChainEmbeddingClient().embed(["原文"])
    with patch("langchain_openai.ChatOpenAI") as chat:
        client = LangChainDeepSeekClient(api_key="fake", model="chat-model", temperature=0)
        client.bind_tools([])
        chat.return_value.bind_tools.assert_called_once_with([])
        assert chat.call_args.kwargs["temperature"] == 0


def test_env_loader_and_setting_precedence(tmp_path, monkeypatch):
    file = tmp_path / "sample.env"
    file.write_text("#注释\nEMPTY=\n =bad\nX_TEST='quoted'\nY_TEST=\"double\"\ninvalid\n", encoding="utf-8")
    monkeypatch.setenv("X_TEST", "existing")
    monkeypatch.delenv("Y_TEST", raising=False)
    assert load_env_file(file) == file
    import os

    assert os.getenv("X_TEST") == "existing" and os.getenv("Y_TEST") == "double"
    assert load_env_file(tmp_path / "missing") is None
    monkeypatch.setenv("DATABASE_URL", "sqlite:///first")
    assert database_path("explicit") == "explicit" and database_path() == "sqlite:///first"
    assert EmbeddingSettings.from_env().as_openai_embeddings_kwargs()["model"] == "qwen3.7-text-embedding-flash"
    assert LLMSettings.from_env().as_chat_openai_kwargs()["model"]


def test_vector_zero_and_normalization():
    assert _normalize_vector([0, 0]) == [0, 0]
    assert _cosine([], [1]) == 0
    assert _cosine([0, 0], [1, 0]) == 0
    assert _cosine([1, 0], [1, 0]) == 1
