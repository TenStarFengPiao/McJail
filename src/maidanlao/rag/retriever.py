"""统一资料范围；小库直投，大库双路召回并可选云端重排。"""

from __future__ import annotations

import json
import logging
import math
from collections import Counter
from typing import Any

from maidanlao.llm.client import LangChainEmbeddingClient
from maidanlao.llm.protocols import RerankClient
from maidanlao.persistence.repository import KnowledgeRepository

from ._shared import CATEGORY_MAP, HISTORY_TERMS, _cosine, _keyword_tokens, _match_tokens
from .chunker import DocumentChunker
from .context import assemble_context, render_evidence_block
from .models import RagResult
from .reranker import rrf_merge

logger = logging.getLogger(__name__)


class HybridRetriever:
    TOP_K = 12
    MAX_CONTEXT_CHARS = 4200

    def __init__(
        self,
        repos: KnowledgeRepository,
        embedding_client=None,
        *,
        context_chars=None,
        rerank_client: RerankClient | None = None,
        direct_context=True,
    ):
        self.repos = repos
        self.embedding_client = embedding_client if embedding_client is not None else LangChainEmbeddingClient()
        self.rerank_client = rerank_client
        self.direct_context = direct_context
        if context_chars is not None:
            self.MAX_CONTEXT_CHARS = max(0, int(context_chars))

    def search(self, question: str) -> RagResult:
        """单通道失败时保留另一通道；所有通道失败才报告检索故障。"""
        query = question.strip()
        if not query:
            return RagResult("insufficient_evidence", 0, [], "", reason="empty_query")
        categories = {
            category
            for category, mapped in CATEGORY_MAP.items()
            if mapped.doc_type != "history_archive" or any(term in query for term in HISTORY_TERMS)
        }
        collections = sorted({CATEGORY_MAP[category].collection_name for category in categories})
        if self.direct_context:
            rows = [
                row
                for row in self.repos.knowledge_chunks.all_with_documents()
                if row["category"] in categories and row["chunk_text"].strip()
            ]
            rows.sort(key=lambda row: (row["document_id"], row["chunk_index"]))
            evidence = [self._evidence(i + 1, 1.0, row) for i, row in enumerate(rows)]
            # 与检索使用同一份资格范围，以实际渲染长度决定是否直投。
            if (
                evidence
                and len("\n\n".join(render_evidence_block(i, ev) for i, ev in enumerate(evidence)))
                <= self.MAX_CONTEXT_CHARS
            ):
                return self._result(evidence, {"path": "whole_corpus", "channel_errors": []})

        errors = []
        ranks = []
        for name, retrieve in (("vector", self._embedding_search), ("keyword", self._bm25_search)):
            try:
                ranks.append(retrieve(query, collections, categories, 30))
            except Exception as exc:
                logger.warning("retrieval_channel_failed channel=%s type=%s", name, type(exc).__name__)
                ranks.append([])
                errors.append({"channel": name, "error": type(exc).__name__})
        if len(errors) == 2:
            return RagResult(
                "retrieval_failed", 0, [], "", reason="all_channels_failed", diagnostics={"channel_errors": errors}
            )
        ranked = [{**item, "rerank_score": item["rrf_score"]} for item in rrf_merge(ranks, 60)]
        rerank_info = {"mode": "rrf"}
        if self.rerank_client is not None and ranked:
            try:
                scores = self.rerank_client.rerank(
                    query, [f"{item['row']['title']}\n{item['row']['chunk_text']}" for item in ranked]
                )
                if len(scores) != len(ranked) or any(not math.isfinite(score) for score in scores):
                    raise ValueError("invalid_rerank_scores")
                ranked = [{**item, "rerank_score": score} for item, score in zip(ranked, scores, strict=True)]
                ranked.sort(key=lambda item: (-item["rerank_score"], item["chunk_id"]))
                rerank_info = {"mode": "cloud"}
            except Exception as exc:
                logger.warning("rerank_rrf_fallback type=%s", type(exc).__name__)
                rerank_info = {"mode": "rrf_fallback", "error": type(exc).__name__}
        evidence = [
            self._evidence(i + 1, item["rerank_score"], item["row"]) for i, item in enumerate(ranked[: self.TOP_K])
        ]
        return self._result(
            evidence,
            {
                "path": "retrieval",
                "rerank": rerank_info,
                "channel_errors": errors,
                "vector_count": len(ranks[0]),
                "keyword_count": len(ranks[1]),
                "ranking": [
                    {"chunk_id": item["chunk_id"], "rank": i + 1, "score": item["rerank_score"]}
                    for i, item in enumerate(ranked)
                ],
            },
        )

    def _result(self, evidence, diagnostics):
        evidence, context, omitted = assemble_context(evidence, self.MAX_CONTEXT_CHARS)
        diagnostics.update(selected_ids=[ev["chunk_id"] for ev in evidence], omitted=omitted)
        return RagResult(
            "ok" if evidence else "insufficient_evidence",
            evidence[0]["rerank_score"] if evidence else 0,
            evidence,
            context,
            tuple(dict.fromkeys(ev["document_id"] for ev in evidence)),
            "" if evidence else "no_context_candidates",
            diagnostics=diagnostics,
        )

    assemble_context = staticmethod(assemble_context)

    def _embedding_search(
        self, query: str, collections: list[str], categories: set[str], top_k: int
    ) -> list[dict[str, Any]]:
        """读取同模型同维度向量并按余弦相似度排序。"""
        embedding_model = self.embedding_client.model_name
        query_vectors = self.embedding_client.embed([query])
        if (
            len(query_vectors) != 1
            or not query_vectors[0]
            or any(not isinstance(value, (int, float)) or not math.isfinite(value) for value in query_vectors[0])
        ):
            raise ValueError("invalid_embedding_response")
        query_vector = query_vectors[0]
        rows = []
        for row in self.repos.knowledge_vectors.list_for_collections(collections):
            if embedding_model and row.get("embedding_model") != embedding_model:
                continue
            if row["category"] not in categories:
                continue
            try:
                vector = json.loads(row["embedding_vector"])
            except json.JSONDecodeError:
                vector = []
            if len(vector) != len(query_vector):
                continue
            score = _cosine(query_vector, vector)
            if score > 0:
                rows.append({"chunk_id": row["chunk_id"], "score": score, "row": row})
        rows.sort(key=lambda item: (-item["score"], item["chunk_id"]))
        return rows[:top_k]

    def _bm25_search(
        self, query: str, collections: list[str], categories: set[str], top_k: int
    ) -> list[dict[str, Any]]:
        """按统一词元口径对关键词索引执行 BM25 排序。"""
        rows = [
            row
            for row in self.repos.knowledge_keywords.list_for_collections(collections)
            if row["category"] in categories
        ]
        if not rows:
            return []
        document_terms = []
        document_frequency: Counter[str] = Counter()
        for row in rows:
            terms = _keyword_tokens(row["chunk_text"] or "")
            document_terms.append((row, terms))
            document_frequency.update(set(terms))
        avg_len = sum(len(terms) for _, terms in document_terms) / max(1, len(document_terms))
        # 查询侧只用双字（_match_tokens），避免单字虚词把 BM25 带偏。
        query_terms = sorted(_match_tokens(query))
        scored = []
        for row, terms in document_terms:
            frequencies = Counter(terms)
            length = len(terms) or 1
            score = 0.0
            for term in query_terms:
                if term not in frequencies:
                    continue
                idf = math.log(
                    1 + (len(document_terms) - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5)
                )
                tf = frequencies[term]
                score += idf * (tf * 2.2) / (tf + 1.2 * (1 - 0.75 + 0.75 * length / max(avg_len, 1)))
            if score > 0:
                scored.append({"chunk_id": row["chunk_id"], "score": score, "row": row})
        scored.sort(key=lambda item: (-item["score"], item["chunk_id"]))
        return scored[:top_k]

    def _evidence(self, evidence_no: int, score: float, row: dict) -> dict:
        """将分块记录转换为带来源、版本和范围的证据对象。"""
        content = row["chunk_text"]
        return {
            "evidence_id": f"证据 {evidence_no}",
            "chunk_id": row["chunk_id"],
            "document_id": row["document_id"],
            "source_title": row["title"],
            "category": row["category"],
            "section_path": row["section_path"],
            # 命中的条款号（用于判断"这次答的是哪一条"，治张冠李戴）。
            "article": DocumentChunker._ARTICLE_RE.findall(content)[:2],
            "content": content,
            "rerank_score": score,
            "document_version": row.get("version", ""),
            "scope": row.get("scope", ""),
            "effective_from": row.get("effective_start", ""),
            "effective_to": row.get("effective_end", ""),
            "publish_date": row.get("publish_date", ""),
            "source": row.get("source", ""),
        }
