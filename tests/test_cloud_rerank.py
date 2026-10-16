"""云端重排契约及失败降级回归，不访问真实 API。"""

import json
from unittest.mock import Mock, patch
from urllib.error import URLError

import pytest

from maidanlao.config.settings import RerankSettings
from maidanlao.llm.rerank import DashScopeRerankClient, RerankError
from maidanlao.persistence.database import open_database
from maidanlao.persistence.repository import KnowledgeRepository
from maidanlao.rag import HybridRetriever, KnowledgeIngestService, LocalHashEmbeddingClient


def test_rerank_request_and_index_mapping():
    settings = RerankSettings("qwen3.7-text-rerank", "test-token", "https://example.com/rerank", 2)
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = json.dumps(
        {"output": {"results": [{"index": 1, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.2}]}}
    ).encode()
    with patch("maidanlao.llm.rerank.urlopen", return_value=response) as send:
        assert DashScopeRerankClient(settings).rerank("报名", ["甲", "乙"]) == [0.2, 0.9]
    request = send.call_args.args[0]
    body = json.loads(request.data)
    assert body["model"] == "qwen3.7-text-rerank"
    assert body["input"] == {"query": "报名", "documents": ["甲", "乙"]}
    assert body["parameters"]["top_n"] == 2
    assert send.call_args.kwargs["timeout"] == 2


@pytest.mark.parametrize(
    "items",
    [
        [],
        [{"index": 1, "relevance_score": 0.9}],
        [{"index": 0, "relevance_score": float("nan")}],
        [{"index": 0, "relevance_score": 0.1}, {"index": 0, "relevance_score": 0.9}],
    ],
)
def test_rerank_rejects_invalid_results(items):
    with pytest.raises(RerankError):
        DashScopeRerankClient._parse_scores({"output": {"results": items}}, 1)


def test_rerank_network_error_is_sanitized():
    settings = RerankSettings("test", "secret", "https://example.com", 1)
    with patch("maidanlao.llm.rerank.urlopen", side_effect=URLError("private error")):
        with pytest.raises(RerankError, match="^rerank_request_failed$"):
            DashScopeRerankClient(settings).rerank("query", ["document"])


@pytest.mark.parametrize("failed", [False, True])
def test_hybrid_retrieval_cloud_order_and_fallback(failed):
    repos = KnowledgeRepository(open_database(":memory:"))
    try:
        for title, text in [("甲通知", "补考报名截止周五。"), ("乙通知", "补考报名地点在教务处。")]:
            KnowledgeIngestService(repos, LocalHashEmbeddingClient()).ingest_text(
                title=title,
                source="教学办",
                publish_date="2026-09-10",
                scope="school",
                category="补考通知",
                raw_text=text,
                created_by=1,
            )
        cloud = Mock()
        cloud.rerank.side_effect = RerankError("unavailable") if failed else lambda query, docs: list(range(len(docs)))
        local = HybridRetriever(repos, LocalHashEmbeddingClient())
        local.direct_context = False
        baseline = local.search("补考报名")
        retriever = HybridRetriever(repos, LocalHashEmbeddingClient(), rerank_client=cloud)
        retriever.direct_context = False
        result = retriever.search("补考报名")
        cloud.rerank.assert_called_once()
        assert result.status == "ok"
        assert len(result.evidence_chunks) == len(baseline.evidence_chunks)
        assert result.diagnostics["channel_errors"] == []
        if failed:
            assert result.diagnostics["rerank"]["mode"] == "rrf_fallback"
            assert result.diagnostics["ranking"] == baseline.diagnostics["ranking"]
        else:
            assert result.diagnostics["rerank"]["mode"] == "cloud"
            assert result.diagnostics["ranking"][0]["chunk_id"] == baseline.diagnostics["ranking"][-1]["chunk_id"]
    finally:
        repos.conn.close()


def test_rerank_settings_key_fallback_and_override(monkeypatch):
    monkeypatch.delenv("RERANK_URL", raising=False)
    assert RerankSettings.from_env().url.endswith("/services/rerank/text-rerank/text-rerank")
    monkeypatch.setenv("EMBEDDING_API_KEY", "embedding-key")
    monkeypatch.setenv("RERANK_API_KEY", "")
    assert RerankSettings.from_env().api_key == "embedding-key"
    monkeypatch.setenv("RERANK_API_KEY", "rerank-key")
    assert RerankSettings.from_env().api_key == "rerank-key"
