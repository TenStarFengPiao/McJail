"""知识文档、分块、索引与评测仓储；通过 SQLAlchemy ORM 访问业务表。"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.sqlite import insert

from maidanlao.persistence.database import Database
from maidanlao.persistence.models import (
    ChatTurn,
    DeliveryResource,
    KnowledgeChunks,
    KnowledgeDocuments,
    KnowledgeKeywordIndexes,
    KnowledgeOriginal,
    KnowledgeVectorIndexes,
    RagTestCases,
)
from maidanlao.persistence.transactions import transaction

_DOCUMENT_FIELDS = (
    "title",
    "source",
    "publish_date",
    "scope",
    "category",
    "doc_type",
    "collection_name",
    "status",
    "version",
    "effective_start",
    "effective_end",
)


def _chunk_record(chunk: KnowledgeChunks, document: KnowledgeDocuments) -> dict[str, Any]:
    """组合分块与来源字段，保持检索结果的原列名契约。"""
    return {**chunk.to_dict(), **{name: getattr(document, name) for name in _DOCUMENT_FIELDS}}


class KnowledgeDocumentRepository:
    """知识文档及版本仓储；替换必须与派生数据共同提交。"""

    def __init__(self, conn: Database):
        self.conn = conn

    def is_active(self, document_id: str) -> bool:
        """检查文档处于有效状态，供来源校验使用。"""
        return (
            self.conn.session.scalar(
                select(KnowledgeDocuments.id).where(
                    KnowledgeDocuments.id == document_id, KnowledgeDocuments.status == "active"
                )
            )
            is not None
        )

    def create_document(self, **kwargs: Any) -> str:
        """保存来源元数据并返回文档 ID；事务内不提前提交。"""
        doc_id = kwargs.get("id") or f"doc_{int(time.time() * 1000)}"
        names = (
            "title",
            "source",
            "publish_date",
            "scope",
            "category",
            "doc_type",
            "source_level",
            "collection_name",
            "priority",
            "file_path",
            "normalized_file_path",
            "created_by",
        )
        self.conn.session.add(
            KnowledgeDocuments(id=doc_id, raw_text=kwargs.get("raw_text", ""), **{name: kwargs[name] for name in names})
        )
        self.conn.session.flush()
        return doc_id

    def replace_document(self, **kwargs: Any) -> str:
        """在调用方事务内替换文档及派生索引，校验预期版本。"""
        doc_id = kwargs["id"]
        # 先取得短写锁，随后核对计算向量期间文档是否已被另一请求更新。
        from sqlalchemy import update

        expected_version = kwargs.get("expected_version")
        if expected_version is not None:
            result = self.conn.session.execute(
                update(KnowledgeDocuments)
                .where(KnowledgeDocuments.id == doc_id, KnowledgeDocuments.version == expected_version)
                .values(version=expected_version + 1)
            )
            if result.rowcount != 1:
                raise ValueError("document_version_changed")
        chunks = select(KnowledgeChunks.chunk_id).where(KnowledgeChunks.document_id == doc_id)
        # 原文覆盖与索引替换同一事务；保留文档 ID，避免旧附件外键阻止重建。
        self.conn.session.execute(delete(KnowledgeKeywordIndexes).where(KnowledgeKeywordIndexes.chunk_id.in_(chunks)))
        self.conn.session.execute(delete(KnowledgeVectorIndexes).where(KnowledgeVectorIndexes.chunk_id.in_(chunks)))
        self.conn.session.execute(delete(KnowledgeChunks).where(KnowledgeChunks.document_id == doc_id))
        document = self.conn.session.get(KnowledgeDocuments, doc_id)
        if document is None:
            return self.create_document(**kwargs)
        for name in (
            "title",
            "source",
            "publish_date",
            "scope",
            "category",
            "raw_text",
            "doc_type",
            "source_level",
            "collection_name",
            "priority",
        ):
            if name in kwargs:
                setattr(document, name, kwargs[name])
        if expected_version is None:
            document.version += 1
        self.conn.session.flush()
        return doc_id

    def get(self, document_id: str):
        """读取文档供重建和来源校验使用。"""
        return self.conn.session.get(KnowledgeDocuments, document_id, populate_existing=True)

    def attach_original(self, document_id: str, filename: str, content: bytes) -> None:
        """关联不可覆盖的原件；在导入事务中调用时与正文共同回滚。"""
        from maidanlao.rag.originals import validate_original

        digest = validate_original(filename, content)
        with transaction(self.conn):
            if self.get(document_id) is None:
                raise ValueError("document_not_found")
            existing = self.original(document_id)
            if existing:
                if existing.filename != filename or existing.sha256 != digest:
                    raise ValueError("original_already_attached")
                return
            self.conn.session.add(
                KnowledgeOriginal(document_id=document_id, filename=filename, content=content, sha256=digest)
            )
            self.conn.session.flush()

    def original(self, document_id: str):
        """仅在交付原件时读取二进制内容。"""
        return self.conn.session.get(KnowledgeOriginal, document_id, populate_existing=True)

    def set_aliases(self, document_id: str, aliases: list[str]) -> None:
        """登记管理员明确指定的简称；不从模型回答自动生成别名。"""
        if any(not isinstance(alias, str) or not alias.strip() for alias in aliases):
            raise ValueError("invalid_document_alias")
        with transaction(self.conn):
            document = self.get(document_id)
            if document is None:
                raise ValueError("document_not_found")
            document.aliases_json = json.dumps(sorted({alias.strip() for alias in aliases}), ensure_ascii=False)

    def list_all(self) -> list[dict[str, Any]]:
        """后台列表不返回大段原文。"""
        original_ids = set(self.conn.session.scalars(select(KnowledgeOriginal.document_id)))
        return [
            {
                **{key: value for key, value in item.to_dict().items() if key != "raw_text"},
                "has_original": item.id in original_ids,
            }
            for item in self.conn.session.scalars(
                select(KnowledgeDocuments).order_by(KnowledgeDocuments.updated_at.desc())
            )
        ]

    def active_documents_for_categories(self, categories: list[str]) -> list[dict[str, Any]]:
        """读取指定分类中有效且已索引的文档。"""
        query = select(KnowledgeDocuments).where(
            KnowledgeDocuments.status == "active",
            KnowledgeDocuments.ingest_status == "indexed",
            KnowledgeDocuments.category.in_(categories),
        )
        return [item.to_dict() for item in self.conn.session.scalars(query)]


class KnowledgeChunkRepository:
    """知识分块及邻接关系仓储。"""

    def __init__(self, conn: Database):
        self.conn = conn

    def create_chunk(
        self,
        document_id: str,
        chunk_id: str,
        chunk_index: int,
        chunk_type: str,
        chunk_text: str,
        chunk_token_count: int,
        prev_chunk_id: str | None,
        next_chunk_id: str | None,
        parent_section_id: str,
        semantic_group_id: str,
        section_path: str = "",
    ) -> None:
        """保存分块与证据元数据；默认值与旧数据库一致。"""
        self.conn.session.add(
            KnowledgeChunks(
                document_id=document_id,
                chunk_id=chunk_id,
                chunk_index=chunk_index,
                chunk_type=chunk_type,
                chunk_text=chunk_text,
                chunk_token_count=chunk_token_count,
                prev_chunk_id=prev_chunk_id,
                next_chunk_id=next_chunk_id,
                parent_section_id=parent_section_id,
                semantic_group_id=semantic_group_id,
                section_path=section_path,
                content_hash=hashlib.sha256(chunk_text.encode("utf-8")).hexdigest(),
            )
        )
        self.conn.session.flush()

    def get_neighbor_ids(self, chunk_id: str) -> list[str | None]:
        """读取前后邻块；不存在的 chunk 与旧接口一致，视为调用错误。"""
        item = self.conn.session.scalar(select(KnowledgeChunks).where(KnowledgeChunks.chunk_id == chunk_id))
        if item is None:
            raise ValueError(f"chunk_not_found:{chunk_id}")
        return [item.prev_chunk_id, item.next_chunk_id]

    def by_chunk_ids(self, chunk_ids: list[str]) -> list[dict[str, Any]]:
        """按请求顺序读取邻块及来源，缺失项忽略。"""
        if not chunk_ids:
            return []
        query = (
            select(KnowledgeChunks, KnowledgeDocuments)
            .join(KnowledgeDocuments, KnowledgeDocuments.id == KnowledgeChunks.document_id)
            .where(KnowledgeChunks.chunk_id.in_(chunk_ids))
        )
        rows = {chunk.chunk_id: _chunk_record(chunk, doc) for chunk, doc in self.conn.session.execute(query)}
        return [rows[key] for key in chunk_ids if key in rows]

    def all_with_documents(self) -> list[dict[str, Any]]:
        """读取有效且已索引文档的全部分块。"""
        query = (
            select(KnowledgeChunks, KnowledgeDocuments)
            .join(KnowledgeDocuments, KnowledgeDocuments.id == KnowledgeChunks.document_id)
            .where(KnowledgeDocuments.status == "active", KnowledgeDocuments.ingest_status == "indexed")
        )
        return [_chunk_record(chunk, doc) for chunk, doc in self.conn.session.execute(query)]


def _index_records(conn: Database, model, collections: list[str]) -> list[dict[str, Any]]:
    """索引与来源统一联查，只返回有效且已入库的指定集合记录。"""
    query = (
        select(model, KnowledgeChunks, KnowledgeDocuments)
        .join(KnowledgeChunks, KnowledgeChunks.chunk_id == model.chunk_id)
        .join(KnowledgeDocuments, KnowledgeDocuments.id == KnowledgeChunks.document_id)
        .where(
            model.collection_name.in_(collections),
            KnowledgeDocuments.status == "active",
            KnowledgeDocuments.ingest_status == "indexed",
        )
    )
    return [{**_chunk_record(chunk, doc), **index.to_dict()} for index, chunk, doc in conn.session.execute(query)]


class KnowledgeVectorIndexRepository:
    """向量索引仓储；唯一键为 chunk_id 与 embedding_model。"""

    def __init__(self, conn: Database):
        self.conn = conn

    def upsert(
        self, chunk_id: str, collection_name: str, embedding_model: str, text_hash: str, vector: list[float]
    ) -> None:
        """原子更新指定模型索引，其他模型向量保留。"""
        vector_id = f"vec_{hashlib.sha256((chunk_id + embedding_model).encode('utf-8')).hexdigest()[:16]}"
        values = dict(
            chunk_id=chunk_id,
            collection_name=collection_name,
            embedding_model=embedding_model,
            embedding_vector_id=vector_id,
            embedding_text_hash=text_hash,
            embedding_vector=json.dumps(vector),
        )
        statement = insert(KnowledgeVectorIndexes).values(**values)
        self.conn.session.execute(
            statement.on_conflict_do_update(
                index_elements=["chunk_id", "embedding_model"],
                set_={
                    **{
                        key: getattr(statement.excluded, key)
                        for key in values
                        if key not in {"chunk_id", "embedding_model"}
                    },
                    "indexed_at": func.current_timestamp(),
                    "updated_at": func.current_timestamp(),
                },
            )
        )
        self.conn.session.expire_all()
        self.conn.session.flush()

    def count(self) -> int:
        """返回全部模型的向量索引数量。"""
        return self.conn.session.scalar(select(func.count()).select_from(KnowledgeVectorIndexes))

    def list_for_collections(self, collections: list[str]) -> list[dict[str, Any]]:
        """读取集合中索引及来源，模型与维度筛选由检索器负责。"""
        return _index_records(self.conn, KnowledgeVectorIndexes, collections)


class KnowledgeKeywordIndexRepository:
    """关键词索引仓储。"""

    def __init__(self, conn: Database):
        self.conn = conn

    def create(self, chunk_id: str, collection_name: str, terms: list[str]) -> None:
        """保存关键词；重复 chunk_id 由数据库唯一约束拒绝。"""
        bm25_id = f"bm25_{hashlib.sha256(chunk_id.encode('utf-8')).hexdigest()[:16]}"
        self.conn.session.add(
            KnowledgeKeywordIndexes(
                chunk_id=chunk_id,
                collection_name=collection_name,
                terms=json.dumps(terms, ensure_ascii=False),
                bm25_document_id=bm25_id,
            )
        )
        self.conn.session.flush()

    def list_for_collections(self, collections: list[str]) -> list[dict[str, Any]]:
        """读取指定集合的有效关键词索引及来源。"""
        return _index_records(self.conn, KnowledgeKeywordIndexes, collections)


class RagTestCaseRepository:
    """检索评测用例仓储。"""

    def __init__(self, conn: Database):
        self.conn = conn

    def list_all(self) -> list[dict[str, Any]]:
        """按创建 ID 顺序返回全部评测用例。"""
        return [item.to_dict() for item in self.conn.session.scalars(select(RagTestCases).order_by(RagTestCases.id))]

    def create(
        self, category: str, question: str, expected_document_id: str, expected_chunk_id: str, required_fact: str = ""
    ) -> int:
        """保存评测期望并返回 ID。"""
        with transaction(self.conn):
            item = RagTestCases(
                category=category,
                question=question,
                expected_document_id=expected_document_id,
                expected_chunk_id=expected_chunk_id,
                required_fact=required_fact,
            )
            self.conn.session.add(item)
            self.conn.session.flush()
            return item.id


class DeliveryResourceRepository:
    """交付资料登记；不调用知识入库器、模型或 Embedding。"""

    def __init__(self, conn: Database):
        self.conn = conn

    def register(
        self, resource_id, title, source, filename, content, keywords, description="", cohort="", auto_send=True
    ):
        """原子登记原始文件及主题词；相同登记幂等，拒绝静默覆盖。"""
        import re

        from maidanlao.rag.originals import validate_original

        digest = validate_original(filename, content)
        if not isinstance(resource_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", resource_id):
            raise ValueError("invalid_resource_id")
        if not title.strip() or not source.strip() or not isinstance(keywords, list) or not keywords:
            raise ValueError("invalid_resource_metadata")
        if any(not isinstance(word, str) or not word.strip() for word in keywords):
            raise ValueError("invalid_resource_keywords")
        metadata = dict(
            title=title,
            source=source,
            filename=filename,
            sha256=digest,
            keywords_json=json.dumps(sorted(set(keywords)), ensure_ascii=False),
            description=description,
            cohort=cohort,
            auto_send=int(auto_send),
        )
        with transaction(self.conn):
            existing = self.get(resource_id)
            if existing:
                if any(getattr(existing, key) != value for key, value in metadata.items()):
                    raise ValueError("resource_already_registered")
                return resource_id
            self.conn.session.add(DeliveryResource(id=resource_id, content=content, **metadata))
        return resource_id

    def get(self, resource_id):
        """按资源 ID 读取交付资料。"""
        return self.conn.session.get(DeliveryResource, resource_id, populate_existing=True)

    def list_all(self):
        """只返回文件元数据；不读取二进制内容。"""
        names = [c for c in DeliveryResource.__table__.columns if c.name != "content"]
        return [dict(row._mapping) for row in self.conn.session.execute(select(*names).order_by(DeliveryResource.id))]


class ChatRepository:
    """会话历史仓储；只提交完整成功的问答轮次。"""

    def __init__(self, conn: Database):
        self.conn = conn

    def recent(self, key: str, limit: int = 8):
        """返回最近轮次，按时间正序用于模型上下文。"""
        query = select(ChatTurn).where(ChatTurn.conversation_key == key).order_by(ChatTurn.id.desc()).limit(limit)
        return list(reversed(self.conn.session.scalars(query).all()))

    def append(self, key: str, question: str, answer: str, sources: list[dict], request_id: str | None = None) -> None:
        """原子保存本轮问答及来源；失败由调用者回滚。"""
        with transaction(self.conn):
            if request_id and self.conn.session.scalar(select(ChatTurn.id).where(ChatTurn.request_id == request_id)):
                return
            self.conn.session.add(
                ChatTurn(
                    conversation_key=key,
                    request_id=request_id,
                    question=question,
                    answer=answer,
                    sources_json=json.dumps(sources, ensure_ascii=False),
                )
            )
            self.conn.session.flush()


class KnowledgeRepository:
    """知识库依赖集合，不包含学生、任务或通知仓储。"""

    def __init__(self, conn: Database):
        self.conn = conn
        self.knowledge_documents = KnowledgeDocumentRepository(conn)
        self.knowledge_chunks = KnowledgeChunkRepository(conn)
        self.knowledge_vectors = KnowledgeVectorIndexRepository(conn)
        self.knowledge_keywords = KnowledgeKeywordIndexRepository(conn)
        self.rag_test_cases = RagTestCaseRepository(conn)
        self.chat = ChatRepository(conn)
        self.resources = DeliveryResourceRepository(conn)
