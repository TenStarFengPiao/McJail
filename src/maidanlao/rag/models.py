"""检索领域数据；与 ORM 实体分离。"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class OriginalAttachment:
    """已定位的原件；仅供入口发送，不进入模型上下文。"""

    document_id: str
    filename: str
    content: bytes


@dataclass
class KnowledgeChunk:
    document_id: str
    chunk_id: str
    chunk_index: int
    chunk_type: str
    chunk_text: str
    chunk_token_count: int
    section_path: str
    parent_section_id: str
    semantic_group_id: str
    prev_chunk_id: str | None = None
    next_chunk_id: str | None = None
    page_start: int | None = None
    page_end: int | None = None


@dataclass(frozen=True)
class DocTypeMapping:
    doc_type: str
    source_level: str
    collection_name: str


@dataclass(frozen=True)
class RagResult:
    status: str
    top_score: float
    evidence_chunks: list[dict]
    final_context: str
    document_ids: tuple[str, ...] = ()
    reason: str = ""
    diagnostics: dict = field(default_factory=dict)
