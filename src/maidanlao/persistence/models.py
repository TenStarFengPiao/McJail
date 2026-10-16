"""业务表的 SQLAlchemy 声明式映射；保持现有列名、默认值及约束。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, LargeBinary, Text, UniqueConstraint, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """业务持久化模型基类；领域 DTO 与 ORM 实体分开。"""

    def to_dict(self) -> dict[str, Any]:
        """返回原列名键值，不把 Session 或 ORM 状态暴露给业务层。"""
        return {
            column.name: getattr(self, self.__mapper__.get_property_by_column(column).key)
            for column in self.__table__.columns
        }


class KnowledgeDocuments(Base):
    """knowledge_documents 表映射；数据访问通过仓储执行。"""

    __tablename__ = "knowledge_documents"

    id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    aliases_json: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'[]'"))
    title: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    publish_date: Mapped[str] = mapped_column(Text, nullable=False)
    effective_start: Mapped[str | None] = mapped_column(Text, nullable=True)
    effective_end: Mapped[str | None] = mapped_column(Text, nullable=True)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    doc_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_level: Mapped[str] = mapped_column(Text, nullable=False)
    collection_name: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_file_path: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    ingest_status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'indexed'"))
    ingest_error: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    created_by: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class KnowledgeOriginal(Base):
    """同一文档的学校原件；字节与校验值独立于检索正文保存。"""

    __tablename__ = "knowledge_originals"

    document_id: Mapped[str] = mapped_column(Text, ForeignKey("knowledge_documents.id"), primary_key=True)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)


class DeliveryResource(Base):
    """无需索引的交付资料；正文不会进入知识块或向量表。"""

    __tablename__ = "delivery_resources"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    keywords_json: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    cohort: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    auto_send: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class KnowledgeChunks(Base):
    """knowledge_chunks 表映射；数据访问通过仓储执行。"""

    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        Index("idx_knowledge_chunk_doc_index", "document_id", "chunk_index", unique=True),
        UniqueConstraint("chunk_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    chunk_id: Mapped[str] = mapped_column(Text, nullable=False)
    document_id: Mapped[str] = mapped_column(Text, ForeignKey("knowledge_documents.id"), nullable=False)
    parent_section_id: Mapped[str] = mapped_column(Text, nullable=False)
    prev_chunk_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_chunk_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    semantic_group_id: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_type: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    section_path: Mapped[str] = mapped_column(Text, nullable=False)
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    evidence_role: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'primary'"))
    created_at: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    program_type: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'普通本科'"))
    credential_type: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'unknown'"))
    article_number: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    condition_group: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    condition_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    requirement_type: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'other'"))
    evidence_type: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'clause'"))


class KnowledgeVectorIndexes(Base):
    """knowledge_vector_indexes 表映射；数据访问通过仓储执行。"""

    __tablename__ = "knowledge_vector_indexes"
    __table_args__ = (UniqueConstraint("chunk_id", "embedding_model"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    chunk_id: Mapped[str] = mapped_column(Text, nullable=False)
    collection_name: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_model: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_vector_id: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_text_hash: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_vector: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'[]'"))
    indexed_at: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    created_at: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class KnowledgeKeywordIndexes(Base):
    """knowledge_keyword_indexes 表映射；数据访问通过仓储执行。"""

    __tablename__ = "knowledge_keyword_indexes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    chunk_id: Mapped[str] = mapped_column(Text, nullable=False)
    collection_name: Mapped[str] = mapped_column(Text, nullable=False)
    terms: Mapped[str] = mapped_column(Text, nullable=False)
    bm25_document_id: Mapped[str] = mapped_column(Text, nullable=False)
    indexed_at: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    created_at: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class RagTestCases(Base):
    """rag_test_cases 表映射；数据访问通过仓储执行。"""

    __tablename__ = "rag_test_cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    expected_document_id: Mapped[str] = mapped_column(Text, nullable=False)
    expected_chunk_id: Mapped[str] = mapped_column(Text, nullable=False)
    required_fact: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class SchemaMigrations(Base):
    """schema_migrations 表映射；数据访问通过仓储执行。"""

    __tablename__ = "schema_migrations"

    version: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    applied_at: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class ChatTurn(Base):
    """完整问答轮次；不保存工具消息、凭据或执行状态。"""

    __tablename__ = "chat_turns"
    __table_args__ = (Index("idx_chat_turn_conversation", "conversation_key", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    conversation_key: Mapped[str] = mapped_column(Text, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    sources_json: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'[]'"))
    created_at: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class DeliveryAttempt(Base):
    """每条 QQ 消息的交付回执；confirmed 仅代表接口确认，不代表用户已读。

    文件与文本页都先登记 sending 再发出；中断或超时留下 unknown/sending，
    同一消息重试时不得自动重发这些结果未知的项目。
    """

    __tablename__ = "delivery_attempts"

    request_id: Mapped[str] = mapped_column(Text, primary_key=True)
    item_key: Mapped[str] = mapped_column(Text, primary_key=True)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class NotificationTask(Base):
    """管理员确认的通知；名单、时间规则及正文模板与聊天历史分开保存。"""

    __tablename__ = "notification_tasks"
    __table_args__ = (Index("idx_notification_due", "status", "next_at"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    source_request_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    owner_id: Mapped[str] = mapped_column(Text, nullable=False)
    conversation: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    spec_json: Mapped[str] = mapped_column(Text, nullable=False)
    recipients_json: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'[]'"))
    expires_at: Mapped[str] = mapped_column(Text, nullable=False)
    next_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class NotificationRun(Base):
    """一次计划执行的不可变正文与发送进度；租约防止多个机器人并发执行。"""

    __tablename__ = "notification_runs"
    __table_args__ = (
        UniqueConstraint("task_id", "scheduled_at"),
        Index("idx_notification_run_status", "status"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    task_id: Mapped[str] = mapped_column(Text, ForeignKey("notification_tasks.id"), nullable=False)
    scheduled_at: Mapped[str] = mapped_column(Text, nullable=False)
    deadline: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    recipients_json: Mapped[str] = mapped_column(Text, nullable=False)
    progress_json: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'{}'"))
    lease_owner: Mapped[str | None] = mapped_column(Text, nullable=True)
    lease_until: Mapped[str | None] = mapped_column(Text, nullable=True)


class ForwardRule(Base):
    """群号规则的确认版本；已有批次保留创建时的目标群。"""

    __tablename__ = "forward_rules"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    source_group: Mapped[str] = mapped_column(Text, unique=True)
    target_group: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    config_json: Mapped[str] = mapped_column(Text)


class ForwardBatch(Base):
    """固定窗口及冻结消息快照；一次发布只使用已确认的预览版本。"""

    __tablename__ = "forward_batches"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    rule_id: Mapped[str] = mapped_column(Text, ForeignKey("forward_rules.id"))
    source_group: Mapped[str] = mapped_column(Text)
    target_group: Mapped[str] = mapped_column(Text)
    opened_at: Mapped[str] = mapped_column(Text)
    closes_at: Mapped[str] = mapped_column(Text, index=True)
    status: Mapped[str] = mapped_column(Text, index=True)
    messages_json: Mapped[str] = mapped_column(Text, default="[]")
    version: Mapped[int] = mapped_column(Integer, default=0)


class ForwardAttachment(Base):
    """只存文件标识与原消息ID，不下载或存储文件字节。"""

    __tablename__ = "forward_attachments"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    batch_id: Mapped[str] = mapped_column(Text, ForeignKey("forward_batches.id"), index=True)
    message_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_id: Mapped[str] = mapped_column(Text)
    filename: Mapped[str] = mapped_column(Text)
    sender_id: Mapped[str] = mapped_column(Text)


class ForwardPreview(Base):
    """正文、目标群与附件选择按版本冻结；旧版本不能确认。"""

    __tablename__ = "forward_previews"
    __table_args__ = (UniqueConstraint("batch_id", "version"),)
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    batch_id: Mapped[str] = mapped_column(Text, ForeignKey("forward_batches.id"))
    version: Mapped[int] = mapped_column(Integer)
    body: Mapped[str] = mapped_column(Text)
    target_group: Mapped[str] = mapped_column(Text)
    attachment_ids_json: Mapped[str] = mapped_column(Text)
