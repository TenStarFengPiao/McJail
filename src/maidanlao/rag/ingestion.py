from __future__ import annotations

import hashlib
import math
import re
import secrets
import time
from typing import Any

from sqlalchemy import select

from maidanlao.llm.client import LangChainEmbeddingClient
from maidanlao.persistence.models import KnowledgeChunks
from maidanlao.persistence.repository import KnowledgeRepository
from maidanlao.persistence.transactions import transaction

from ._shared import (
    _match_tokens,
    map_category_to_doc_type,
    strip_front_matter,
)
from .chunker import DocumentChunker
from .normalizer import DocumentNormalizer


class KnowledgeIngestService:
    """文档入库用例；文档、分块与派生索引共同提交或回滚。"""

    def __init__(self, repos: KnowledgeRepository, embedding_client=None):
        self.repos = repos
        self.chunker = DocumentChunker()
        self.embedding_client = embedding_client if embedding_client is not None else LangChainEmbeddingClient()
        self.normalizer = DocumentNormalizer()

    def reingest_document(self, document_id: str) -> str:
        """按原 id 重新入库某一篇文档。

        源文本优先取数据库保存的原文；旧文档取 ``normalized_file_path``，
        取不到则退回 ``file_path``。用于分块规则/元数据策略变更后重建索引；
        关键词索引与向量都会重算（按分块执行 embedding 调用）。
        """
        from pathlib import Path

        row = self._find_document(document_id)
        if row is None:
            raise ValueError(f"document_not_found:{document_id}")
        root = Path(__file__).resolve().parents[3]
        source_text = row.get("raw_text") or None
        for key in () if source_text else ("normalized_file_path", "file_path"):
            candidate = row.get(key)
            if not candidate:
                continue
            path = root / candidate
            if path.is_file():
                source_text = path.read_text(encoding="utf-8")
                break
        if source_text is None:
            # 没有落盘副本时，用库里已存的块拼回原文（会带上旧的元数据头，可接受）。
            texts = self.repos.conn.session.scalars(
                select(KnowledgeChunks.chunk_text)
                .where(KnowledgeChunks.document_id == document_id)
                .order_by(KnowledgeChunks.chunk_index)
            ).all()
            if not texts:
                raise ValueError(f"document_source_unavailable:{document_id}")
            source_text = "\n".join(texts)
        return self.ingest_text(
            document_id=document_id,
            expected_version=row["version"],
            title=row["title"],
            source=row["source"],
            publish_date=row["publish_date"],
            scope=row["scope"],
            category=row["category"],
            raw_text=source_text,
            created_by=int(row["created_by"] or 0),
        )

    def _find_document(self, document_id: str) -> dict[str, Any] | None:
        document = self.repos.knowledge_documents.get(document_id)
        return document.to_dict() if document else None

    def reingest_text(
        self,
        document_id: str,
        title: str,
        source: str,
        publish_date: str,
        scope: str,
        category: str,
        raw_text: str,
        created_by: int,
    ) -> str:
        """按原 id 覆盖重建一篇文档。

        用途：分块规则或元数据策略变更后重新入库（关键词索引与向量都会重算）。
        代价是重新生成所有分块的向量，所以只对指定文档执行。
        """
        row = self._find_document(document_id)
        if row is None:
            raise ValueError(f"document_not_found:{document_id}")
        return self.ingest_text(
            title,
            source,
            publish_date,
            scope,
            category,
            raw_text,
            created_by,
            document_id=document_id,
            expected_version=row["version"],
        )

    def ingest_text(
        self,
        title: str,
        source: str,
        publish_date: str,
        scope: str,
        category: str,
        raw_text: str,
        created_by: int,
        document_id: str | None = None,
        original_name: str | None = None,
        original_content: bytes | None = None,
        expected_version: int | None = None,
    ) -> str:
        """事务外批量计算向量；短事务内原子保存原文、分块和索引。"""
        session = self.repos.conn.session
        if session.in_transaction():
            from sqlalchemy.orm import SessionTransactionOrigin

            if (
                session.get_transaction().origin != SessionTransactionOrigin.AUTOBEGIN
                or session.new
                or session.dirty
                or session.deleted
            ):
                raise RuntimeError("ingestion_requires_independent_transaction")
            session.rollback()
        if document_id is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", document_id):
            raise ValueError("invalid_document_id")
        if (original_name is None) != (original_content is None):
            raise ValueError("incomplete_original")
        mapped = map_category_to_doc_type(category)
        normalized = self.normalizer.normalize_text(
            raw_text,
            {
                "title": title,
                "source": source,
                "publish_date": publish_date,
                "scope": scope,
                "category": category,
                "doc_type": mapped.doc_type,
                "source_level": mapped.source_level,
                "collection_name": mapped.collection_name,
            },
        )
        # 文档 id 加随机后缀：只用毫秒时间戳时，连续两次入库会撞 id
        # （实测在同一毫秒内 ingest 两篇文档直接 UNIQUE 冲突）。
        doc_id = document_id or f"doc_{int(time.time() * 1000)}_{secrets.token_hex(3)}"
        # 元数据存表即可，不再写进 chunk 正文（否则每块约 250 字是元数据，
        # 会把向量相似度压到 0.25~0.33，并让 rerank 的匹配被公文抬头带偏）。
        body = strip_front_matter(normalized.markdown)
        if original_name is not None:
            from .originals import validate_original

            validate_original(original_name, original_content)
        chunks = self.chunker.split_text(doc_id, f"{doc_id}_sec_001", title, body)
        dimension = None
        if not chunks:
            raise ValueError("empty_document")
        vectors = self.embedding_client.embed([chunk.chunk_text for chunk in chunks])
        if len(vectors) != len(chunks):
            raise ValueError("invalid_embedding_response")
        for vector in vectors:
            if not vector or any(
                not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) for v in vector
            ):
                raise ValueError("invalid_embedding_response")
            if dimension is not None and dimension != len(vector):
                raise ValueError("embedding_dimension_changed")
            dimension = len(vector)
        with transaction(self.repos.conn):
            create = (
                self.repos.knowledge_documents.replace_document
                if expected_version is not None
                else self.repos.knowledge_documents.create_document
            )
            doc_id = create(
                id=doc_id,
                title=title,
                source=source,
                publish_date=publish_date,
                scope=scope,
                category=category,
                doc_type=mapped.doc_type,
                source_level=mapped.source_level,
                collection_name=mapped.collection_name,
                priority="学院通知" if mapped.source_level == "college" else mapped.source_level,
                file_path=f"raw/{doc_id}.md",
                normalized_file_path=f"normalized/{doc_id}.md",
                created_by=created_by,
                raw_text=raw_text,
                expected_version=expected_version,
            )
            if original_name is not None:
                self.repos.knowledge_documents.attach_original(doc_id, original_name, original_content)
            for chunk, vector in zip(chunks, vectors, strict=True):
                content_hash = hashlib.sha256(chunk.chunk_text.encode("utf-8")).hexdigest()
                self.repos.knowledge_chunks.create_chunk(
                    doc_id,
                    chunk.chunk_id,
                    chunk.chunk_index,
                    chunk.chunk_type,
                    chunk.chunk_text,
                    chunk.chunk_token_count,
                    chunk.prev_chunk_id,
                    chunk.next_chunk_id,
                    chunk.parent_section_id,
                    chunk.semantic_group_id,
                    chunk.section_path,
                )
                self.repos.knowledge_keywords.create(
                    chunk.chunk_id, mapped.collection_name, sorted(_match_tokens(chunk.chunk_text))
                )
                self.repos.knowledge_vectors.upsert(
                    chunk.chunk_id, mapped.collection_name, self.embedding_client.model_name, content_hash, vector
                )
        return doc_id
